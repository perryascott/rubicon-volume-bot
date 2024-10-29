import os, time, threading, argparse, atexit, random

from typing import Union, Dict
from flask import Flask
from multiprocessing import Queue
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler

from rubi import Union, NewLimitOrder, Transaction, OrderSide, EmitTakeEvent, EmitCancelEvent, UpdateLimitOrder
from rubi import Client, OrderBook, OrderEvent, EmitOfferEvent, NewCancelOrder, OrderType, ERC20
from _decimal import Decimal
from web3 import Web3

from transactionLogging import Logger
from events import OrderBookRequester, LastCancelTimes, PolledOrder
from pairs import TokenPairs, OrderComparison, BestPrices
from utils import TokenPrice, get_client, BalanceNotification, ErrorNotification
from swap import Uniswapper


##### Read in Argparse/Configurations #####

parser = argparse.ArgumentParser()
parser.add_argument('--pair', type=str, help='which token pair to run on')
parser.add_argument('--min_spread', action='store_true', help='does small spread pricing strategy')
parser.add_argument('--cancel', action='store_true', help='cancels not-best orders if out of balance')
parser.add_argument('--cancel_old', type=int, help='cancels not-best orders if out of balance and if older than arg minutes')
parser.add_argument('--loop_time', type=int, help='minutes to call order_loop')
parser.add_argument('--alert_time', type=int, default=15, help="Minimum notification time")
parser.add_argument('--swap',  action='store_true', help='uses uniswap')
parser.add_argument('--no_arb',  action='store_true', help='uses anti-arb loop')
parser.add_argument('--tack',  action='store_true', help='tack method')
parser.add_argument('--cancel_all', type=int, help="Number of minutes before every order is cancelled")


args = parser.parse_args()
load_dotenv(".email_env")

token = None
match args.pair:
	case "weth_usdc":
		token = TokenPairs.WETH_USDC
		token_port = 5000
		market_price = TokenPrice(token=token)
		gas_price = market_price

	case "weth_usdt":
		token = TokenPairs.WETH_USDT
		token_port = 5001
		# TODO: fix this
		market_price = TokenPrice(token=token)
		gas_price = market_price

	case "weth_dai":
		token = TokenPairs.WETH_DAI
		token_port = 5002
		# TODO: fix this
		market_price = TokenPrice(token=token)
		gas_price = market_price

	case "usdc_dai":
		token = TokenPairs.USDC_DAI
		token_port = 5003
		market_price = TokenPrice(token=token)
		gas_price = TokenPrice(token=TokenPairs.WETH_USDC)

	case "op_usdc":
		token = TokenPairs.OP_USDC
		token_port = 5004
		market_price = TokenPrice(token=token)
		gas_price = TokenPrice(token=TokenPairs.WETH_USDC)

	case "weth_usdc_arb":
		token = TokenPairs.WETH_USDC_ARB
		token_port = 5005
		market_price = TokenPrice(token=token)
		gas_price = market_price

if token is None:
	raise ValueError("Token not correctly set")

##### Global Objects #####

# Loggers, clients, and notifiers
app = Flask(__name__)
my_logger = Logger(token=token)
balance_notifier = BalanceNotification(args.alert_time)
gas_notifier = BalanceNotification(args.alert_time)
error_notifier = ErrorNotification()
cancel_times = LastCancelTimes(args.cancel_old)
my_queue = Queue()
client = get_client(queue=my_queue, pair=token)

# Balance Estimation 
base_erc20 = ERC20.from_network(token.sign_list()[0], network=client.network)
quote_erc20 = ERC20.from_network(token.sign_list()[1], network=client.network)

# TODO: 2. figure out how to measure gas on arbitrum
if token==TokenPairs.WETH_USDC_ARB:
	gas_erc20 = ERC20.from_network("WETH", network=client.network)
else:
	gas_erc20 = ERC20.from_network("ETH", network=client.network)

gas_warning_threshold = Decimal('5') # in USD
gas_error_threshold = Decimal('1') # in USD

base_allowance = token.target_allowances()
start_spread_buffer = Decimal("3") 

# Orderbook Poller
order_book_poller = OrderBookRequester(client=client,token=token)

# Uniswap client
uniswapper = Uniswapper(pair=token, 
			quoteERC20=quote_erc20, 
			baseERC20=base_erc20, 
			gasERC20=gas_erc20, 
			market_price=market_price, 
			gas_price=gas_price, 
			beta=token.beta(),
			logger=my_logger)

# Threshold percentage calculations
alpha = token.alpha() # Larger alpha is more aggressive
gamma = token.gamma() # Larger gamma is more aggressive
ask_thresh_percent = Decimal(1) - alpha
bid_thresh_percent = Decimal(1) + alpha

if alpha <= 0:
	raise ValueError("alpha cannot be less than or equal to 0")

# Gamma must be less than alpha
if gamma >= alpha:
	raise ValueError("gamma cannot be larger than alpha")

###### Helper Functions ######

# Calls update of market/gas price objects
def update_market_price() -> None:
	if token == TokenPairs.USDC_DAI or token == TokenPairs.OP_USDC:
		gas_price.update_price()
	market_price.update_price()

# Listens for events on orderbook
def rubicon_listener(queue: Queue) -> None: 
	while True:
		message: Union[OrderBook, OrderEvent] = queue.get(block=True)
		if isinstance(message, OrderEvent):
			if message.pair_name == token.sign():
				on_order(order=message)
		else:
			raise Exception("rubicon_listener: Unexpected message fetched from queue")
		time.sleep(0.1)

# Turns the spread value from ints into an integer base on order size
def convert_spread_ints(quote_ints: int, size: Decimal) -> Decimal:
	return  Decimal(quote_ints) / Decimal(10**quote_erc20.decimal) / size

# Converts a Price to an integer value for an order to be placed
def price_to_ints(price: Decimal, size: Decimal, side: OrderSide, set_closest: bool = False) -> int:
	ints_unrounded = price * size * Decimal(10**quote_erc20.decimal)
	if side == OrderSide.BUY:
		ints = int(ints_unrounded)
		if set_closest and ints_unrounded == ints:
			ints -= 1
		# ints = int(ints_unrounded - start_spread_buffer / Decimal('2') + Decimal('1')) 

	elif side == OrderSide.SELL:
		# ints = int(ints_unrounded + start_spread_buffer / Decimal('2') - Decimal('1')) 
		ints = int(ints_unrounded) + 1
	return ints

# Returns true if action required for a side (no order of not best)
def requires_action(order_comparison:OrderComparison) -> bool:
	return order_comparison == OrderComparison.NO_ORDERS or order_comparison == OrderComparison.NOT_BEST

# Check that global variables have been set:
def globals_are_none() -> bool:
	return order_book_poller.book_best_ask is None or order_book_poller.book_best_bid is None or market_price.price is None or gas_price.price is None

# Check for adequate gas and send necesssary message
def enough_gas() -> bool:
	# TODO: GAS remove this
	if token==TokenPairs.WETH_USDC_ARB:
		return True
	
	# In Eth
	gas_balance = gas_erc20.to_decimal(number=gas_erc20.balance_of(account=os.getenv("WALLET")))
	gas_to_dollars = gas_balance * gas_price.price
	if gas_to_dollars < gas_error_threshold:
		subject = f"GAS ERROR in {token.sign()} account."
		message = f"Gas has ETH -> USD value of {gas_to_dollars} $ "
		gas_notifier.send_notification(subject=subject, message=message)
		return False
	return True

# Check for adequate gas and send necesssary message
def enough_balance(order_side: OrderSide) -> bool:

	# Check for enough quote asset in balance
	if order_side == OrderSide.SELL:
		base_balance = base_erc20.to_decimal(number=base_erc20.balance_of(account=os.getenv("WALLET")))
		if base_balance < base_allowance :
			return False
	
	# Check for enough quote asset in balance
	elif order_side == OrderSide.BUY:
		quote_balance = quote_erc20.to_decimal(number=quote_erc20.balance_of(account=os.getenv("WALLET")))
		quote_to_base_balance = quote_balance / market_price.price
		if quote_to_base_balance  < base_allowance * Decimal('1.02'): # Add 1% for conversion/roudning errors error
			return False
		
	return True

# Calculate how much amount is needed
def get_remainder(order_side: OrderSide) -> bool:

	# Check for enough quote asset in balance
	if order_side == OrderSide.SELL:
		base_balance = base_erc20.to_decimal(base_erc20.balance_of(account=os.getenv("WALLET")))
		print("base_")
		swap_needed_in_base = (base_allowance * Decimal('1.02')) - base_balance
		swap_amt_in_quote = int(swap_needed_in_base * market_price.price * Decimal(10 ** quote_erc20.decimal))
		print(f"base_balance = {base_balance} | swap_needed_in_base = {swap_needed_in_base} | swap_amt_in_quote = {swap_amt_in_quote}")
		return swap_amt_in_quote
	
	# Check for enough quote asset in balance
	elif order_side == OrderSide.BUY:
		quote_balance = quote_erc20.to_decimal(quote_erc20.balance_of(account=os.getenv("WALLET")))
		base_allowance_to_quote = base_allowance * market_price.price
		swap_needed_in_quote = (base_allowance_to_quote * Decimal('1.02')) - quote_balance
		swap_amt_in_base = int(swap_needed_in_quote / market_price.price * Decimal(10 ** base_erc20.decimal))
		print(f"quote_balance | {quote_balance} | base_allowance_to_quote = {base_allowance_to_quote} | swap_needed_in_quote = {swap_needed_in_quote} | swap_amt_in_base = {swap_amt_in_base}")
		return swap_amt_in_base

# Sends balance notification to phone
def balance_notification(order_side: OrderSide) -> None:
	if order_side == OrderSide.SELL:
		base_balance = base_erc20.to_decimal(number=base_erc20.balance_of(account=os.getenv("WALLET")))
		subject = f" BALANCE ERROR: {token.sign()} account out of {token.sign_list()[0]}."
		message = f"{token.sign_list()[0]} has value of {base_balance} {token.sign_list()[0]}"
		message += f"\n\n Base order size: {base_allowance} {token.sign_list()[0]}"
		balance_notifier.send_notification(subject=subject, message=message)

	elif order_side == OrderSide.BUY:
		quote_balance = quote_erc20.to_decimal(number=quote_erc20.balance_of(account=os.getenv("WALLET")))
		quote_to_base_balance = quote_balance / market_price.price
		subject = f"BALANCE ERROR: {token.sign()} account out of {token.sign_list()[1]}."
		message = f"{token.sign_list()[1]} has value of {quote_to_base_balance} {token.sign_list()[0]}"
		message += f"\n\n Base order size: {base_allowance} {token.sign_list()[0]}"
		balance_notifier.send_notification(subject=subject, message=message)

def get_volume(order: OrderEvent):
	if order.order_side == OrderSide.BUY:
		my_logger.bid_my_price.append(order.price)
		my_logger.bid_volume.append(order.size)
		my_logger.bid_market_price.append(market_price.price)
	elif order.order_side == OrderSide.SELL:
		my_logger.ask_my_price.append(order.price)
		my_logger.ask_volume.append(order.size)
		my_logger.ask_market_price.append(market_price.price)

##### Order Triggers #####

def on_orderbook_action(order: OrderEvent) -> None:
	match order.order_type:

		case OrderType.MARKET:
			pass
		case OrderType.LIMIT:
			pass
		case OrderType.LIMIT_TAKEN:
			pass

		case OrderType.LIMIT_DELETED:
			if order.market_order_owner != os.getenv("WALLET"):
				order_loop()

		case OrderType.CANCEL:
			if order.market_order_owner != os.getenv("WALLET"):
				order_loop()

# Handles my incoming market orders
def on_order(order: OrderEvent) -> None:
	match order.order_type:

		case OrderType.MARKET:
			print(f"EVENT: MARKET ORDER PLACED: \n{order} ")

		case OrderType.LIMIT:
			print(f"EVENT: LIMIT ORDER PLACED: \n{order}")

		case OrderType.LIMIT_TAKEN:
			if order.market_order_owner == os.getenv("WALLET"):
				print(f"ERROR ERROR - on_order: fulfilling own orders")
				error_notifier.send_notification(f"FULFILLING OWN ORDERS ON {token}","title")
				my_logger.self_takes.append(order.size*order.price)
			
			if order.limit_order_owner == os.getenv("WALLET"):
				print(f"EVENT: LIMIT ORDER TAKEN: \n{order} ")
				get_volume(order)

		case OrderType.LIMIT_DELETED:
			if order.market_order_owner == os.getenv("WALLET"):
				print(f"ERROR ERROR - on_order: fulfilling own orders")
				error_notifier.send_notification(f"FULFILLING OWN ORDERS ON {token}","title")
				return
			if order.limit_order_owner == os.getenv("WALLET"):
				print(f"EVENT: LIMIT ORDER DELETED: \n{order}")
			order_loop()

		case OrderType.CANCEL:
			if order.limit_order_owner == os.getenv("WALLET"):
				print(f"EVENT: LIMIT ORDER CANCELLED: \n{order}")
			order_loop()

# Check that by orders are the best on the market
def check_best(order_side: OrderSide, size: Decimal) -> OrderComparison:

	poll_trys = 0
	while True:
		poll_success = order_book_poller.poll_book()
		if poll_trys >= 10:
			print(f"ERROR - check_best: could not retrieve orderbook orders after {poll_trys} trys.")
			return OrderComparison.ERROR_RETRIEVING
		if poll_success:
			break
		poll_trys += 1
		print(f"WARNING - check_best: polled {poll_trys} times.")
		time.sleep(5)
		
	# Double check that prices were recent
	if not order_book_poller.is_poll_recent():
		print(f"ERROR - check_best: could not retrieve orderbook orders after {poll_trys} trys.")
		return OrderComparison.ERROR_RETRIEVING

	# Check that asks/bids are not sitting below/above threshold values
	if order_book_poller.book_best_ask.price <= market_price.price*(Decimal(1) - alpha) or \
	   order_book_poller.book_best_bid.price >= market_price.price*(Decimal(1) + alpha):
		return OrderComparison.THRESHOLD_PRICES

	if order_side == OrderSide.BUY:

		# No existing order
		if order_book_poller.my_best_bid is None:
			return OrderComparison.NO_ORDERS
		
		# Not the best order on the market
		elif order_book_poller.my_best_bid.price < order_book_poller.book_best_bid.price:
			return OrderComparison.NOT_BEST
		
	elif order_side == OrderSide.SELL:

		# No existing order
		if order_book_poller.my_best_ask is None:
			return OrderComparison.NO_ORDERS
		
		# Not the best order on the market
		elif order_book_poller.my_best_ask.price > order_book_poller.book_best_ask.price:
			return OrderComparison.NOT_BEST
		
	# I have the best orders
	return OrderComparison.BEST

# -1 means error, 0 means no orders to cancel, 1 means success
def cancel_orders(order_side: OrderSide) -> int:

	# Check --cancel_old argument
	if args.cancel_old:
		if not cancel_times.can_cancel(order_side=order_side):
			print(f"\t\tcancel_orders: too early to cancel on {order_side}, must wait {args.cancel_old} mins")
			my_logger.cancel_prevented += 1
			return -1

	if order_side == OrderSide.BUY:
		cancel_orders = order_book_poller.all_my_bids
	elif order_side == OrderSide.SELL:
		cancel_orders = order_book_poller.all_my_asks
	
	if len(cancel_orders) < 1:
		print(f"\t\tcancel_orders: no orders to cancel on {order_side} side.")
		my_logger.insufficient_balance.append(order_side)
		return 0
	
	all_cancel_transactions = []
	for order in cancel_orders:
		all_cancel_transactions.append(NewCancelOrder(token.sign(),order_id = int(order.limit_order_id,16)))

	transaction=Transaction(orders=all_cancel_transactions)
	transaction_reciept = client.batch_cancel_limit_orders(transaction)
	print("cancel transaction reciept=", transaction_reciept)

	if transaction_reciept.status == 1:
		print(f"\t\tcancel_orders: succesfully cancelled orders on {order_side} side.")
		my_logger.cancel.append(order_side)
		return 1
	else:
		print(f"\t\tcancel_orders: failed to cancel orders on {order_side} side.")
		my_logger.cancel_failed += 1
		return -1
	
def cancel_all():
	print(f"cancel_all: cancelling all orders.")
	cancel_orders(order_side=OrderSide.BUY)
	cancel_orders(order_side=OrderSide.SELL)

# def cancel_not_best(order_side: OrderSide) -> bool:
# 	return False

def arb_checker():
	# Check that global variables have been set:
	if globals_are_none():
		print(f"ERROR - arb_checker: globals are none.")
		my_logger.price_api_error += 1
		return 

	# TODO: make this apart of the poll_book function
	poll_trys = 0
	while True:
		poll_success = order_book_poller.poll_book()
		if poll_trys >= 10:
			print(f"ERROR - check_best: could not retrieve orderbook orders after {poll_trys} trys.")
			return OrderComparison.ERROR_RETRIEVING
		if poll_success:
			break
		poll_trys += 1
		print(f"WARNING - check_best: polled {poll_trys} times.")
		time.sleep(2)
	
	orders_to_cancel = []
	
	for my_ask in order_book_poller.all_my_asks:
		if my_ask.price < market_price.price*(Decimal(1) - alpha):
			orders_to_cancel.append(NewCancelOrder(token.sign(),order_id = int(my_ask.limit_order_id,16)))

	for my_bid in order_book_poller.all_my_bids:
		if my_bid.price > market_price.price*(Decimal(1) + alpha):
			orders_to_cancel.append(NewCancelOrder(token.sign(),order_id = int(my_bid.limit_order_id,16)))

	if len(orders_to_cancel) > 0:
		try:
			transaction=Transaction(orders=orders_to_cancel)
			transaction_reciept = client.batch_cancel_limit_orders(transaction)
			print("arbitrage cancel reciept=", transaction_reciept)

			if transaction_reciept.status == 1:
				print(f"\t\tarb_checker: succesfully cancelled {len(orders_to_cancel)} orders. ")
				my_logger.arb_cancel += len(orders_to_cancel)
			else:
				print(f"\t\tarb_checker: failed to cancel {orders_to_cancel}")
				my_logger.cancel_failed += 1
		except Exception as e:
			print(f"\t\tarb_checker: error cancelling {orders_to_cancel}")
			print(e)
			my_logger.cancel_failed += 1	
	# else:
	# 	print(f"\t\tarb_checker: No orders cancelled")

def set_limit(order_side: OrderSide, order_quality_status: OrderComparison, set_closest: bool) -> Union[None, Dict]:

	print(f"\t\tset_limit: called on {order_side}")
	# Check quality of my existing offers compared to market place
	match order_quality_status:

		case OrderComparison.BEST:
			print(f"\t\tset_limit: best on {order_side}, do nothing.")
			my_logger.best_offer += 1
			return 
		
		case OrderComparison.NOT_BEST:
			print(f"\t\t set_limit: NOT best on {order_side}, cancel and replace.")
			print("\n\n\n")
			is_not_best = True
		
		case OrderComparison.NO_ORDERS:
			print(f"\t\tset_limit: no offers on {order_side}, place orders.")
			is_not_best = False

		case OrderComparison.ERROR_RETRIEVING:
			print(f"\t\tset_limit: Rubicon API Failed, do nothing.")
			my_logger.rubi_api_error += 1
			return 
		
		case OrderComparison.THRESHOLD_PRICES:
			print(f"\t\tset_limit: Bids/asks are above/below threshold.")
			my_logger.thresholds += 1
			return 

	# Check that global variables have been set:
	if globals_are_none():
		print(f"ERROR - set_limit: globals are none.")
		my_logger.price_api_error += 1
		return 

	# Check if enough gas to execute trade
	if not enough_gas():
		print(f"ERROR - set_limit: not enough gas.")
		my_logger.insufficient_gas += 1
		return

	# Enough funds to execute trade
	if enough_balance(order_side=order_side):
		order_size = base_allowance
	
	# Not enough funds to execute trade
	else:
		print(f"\t\tset_limit: Balance is low on {order_side}, cancel/swap.")

		# Don't do this if not using --cancel
		if not args.cancel and args.cancel_old is None:
			my_logger.insufficient_balance.append(order_side)
			balance_notification(order_side)
			print(f"ERROR - set_limit: Cancel prevented and balance is low on {order_side}.")
			return
		
		# Cancel orders on order_side and record success
		print(f"\t\tset_limit: attempting to cancel orders on {order_side} side")
		successful_cancel = cancel_orders(order_side=order_side)
		
		# Cancel function either had an error, no orders to cancel, or still not enough funds
		if not enough_balance(order_side=order_side) and args.swap:
			print(f"\t\tset_limit: attempting uniswap on {order_side} side")
			trade_amt = get_remainder(order_side=order_side)
			result = uniswapper.swap(side=order_side, trade_amt=trade_amt, base_allowance=base_allowance, set_closest=set_closest)

			# Uniswap error occurred
			if result == -1:
				print(f"\t\tset_limit: uniswap on {order_side} side error occured")
				my_logger.swap_error += 1
				return
			
			# Not enough funds to swap
			elif result == 0:
				my_logger.insufficient_swaps.append(order_side)
				
				# Return if set_closest so other sides best orders aren't cancelled
				if set_closest:
					print(f"\t\tset_limit: not enough funds and not cancelling on other side {order_side} because set_closest")
					return
				else:
					print(f"\t\tset_limit: attempting cancel to swap; not enough funds to swap on uniswap on {order_side} side (remember uniswap is flipped)")

				# Attempt to cancel orders on other side
				if order_side==OrderSide.BUY:
					swap_cancel_side = OrderSide.SELL
				elif order_side==OrderSide.SELL:
					swap_cancel_side = OrderSide.BUY
				successful_cancel = cancel_orders(order_side=swap_cancel_side)

				# If orders were succesfully cancelled, uniswap again
				if successful_cancel == 1:
					print(f"\t\tset_limit: swap-cancel on {swap_cancel_side} side succesful. Sleeping and placing")
					time.sleep(5)
					result = uniswapper.swap(side=order_side, base_allowance=base_allowance, set_closest=set_closest)

					# Uniswap error occurred
					if result == -1:
						print(f"\t\tset_limit: uniswap on {order_side} side error occured")
						my_logger.swap_error += 1
						return
					
					# Still not enough funds for some reason, this would happen if uniswap was slow or cancel orders were partially filled
					elif result == 0:
						my_logger.insufficient_swaps_again.append(order_side)
						print(f"\t\tet_limit: still not enough funds to swap on uniswap {order_side} side (remember uniswap is flipped). Probably orders weren't actually cancelled yet? ")
						return      
					
					my_logger.cancel_then_swaps += 1
					print(f"\t\tset_limit: WOW! Specific cancel, then swap condition succeeded!")

			print(f"\t\tset_limit: uniswap on {order_side} side successful")
			my_logger.uniswap_sides.append(order_side)
		order_size = base_allowance
	
	# Get book best asks/bids
	best_ask = order_book_poller.book_best_ask.price 
	best_bid = order_book_poller.book_best_bid.price
	
	# Get spread in ints
	spread = order_book_poller.book_best_ask.price  - order_book_poller.book_best_bid.price
	spread_buffer_price = convert_spread_ints(quote_ints=start_spread_buffer, size=order_size)

	if args.tack:
		# Find which edge prices are furthest from market price
		if best_bid + spread_buffer_price/2 > market_price.price*(Decimal(1) - gamma):
			edge_bid = best_bid + spread_buffer_price/2
		else:
			edge_bid = market_price.price*(Decimal(1) - gamma)
		if best_ask - spread_buffer_price/2 < market_price.price*(Decimal(1) + gamma):
			edge_ask = best_ask - spread_buffer_price/2
		else:
			edge_ask = market_price.price*(Decimal(1) + gamma)

		# Go with lower end
		if market_price.price - edge_bid > edge_ask - market_price.price:
			target_price = edge_bid
		# Go with upper end
		else:
			target_price = edge_ask
	else:
		target_price = market_price.price
			
	# Get target price
	# Tacking method
	# if tack_value is None:
	# 	# Just use market price
	# 	target_price = market_price.price
	# elif tack_value:
	# 	# Go to top end of the spread
	# 	# Check if threshold price or best_ask is lower
	# 	if best_ask - spread_buffer_price/2 < market_price.price*(Decimal(1) + alpha):
	# 		target_price = best_ask - spread_buffer_price/2
	# 	else:
	# 		target_price = market_price.price*(Decimal(1) + alpha)
	# else:
	# 	# Go to bottom end of the spread
	# 	# Check if threshold price or best_bid is higher
	# 	if best_bid + spread_buffer_price/2 > market_price.price*(Decimal(1) - alpha):
	# 		target_price = best_bid + spread_buffer_price/2
	# 	else:
	# 		target_price = market_price.price*(Decimal(1) - alpha)

	# if the min_spread_buffer is not small enough, return
	if set_closest and spread <= spread_buffer_price/2:
		print(f"\t\tset_limit: (set_closest active) Very small spread detected || spread = {spread} || spread_buffer_price = {spread_buffer_price/2}")
		my_logger.spread_small += 1
		return
	elif spread <= spread_buffer_price:
		print(f"\t\tset_limit: Very small spread detected || spread = {spread} || spread_buffer_price = {spread_buffer_price}")
		my_logger.spread_small += 1
		return

	if order_side == OrderSide.SELL:
		# Condition 0: set_limit is true and best_bid is not going to get me arbed
		if set_closest and best_bid > market_price.price * ask_thresh_percent:
			limit_ask_price = best_bid 
			# get the int value from best_bid price and size and round up (keep in mind this may need adding up if it's exactly an int)
			print("\t\tset_limit: Condition 0 Hit")    
		# Condition 1: starting spread is large, index is within buffered top ask and bid
		#TODO: added "=" to all "<" and ">". See if this causes problems
		elif target_price <= best_ask - spread_buffer_price/2 and target_price >= best_bid + spread_buffer_price/2:
			limit_ask_price = target_price 
			# get the int value from best_bid price and size and round down (keep in mind this may need rounding down if it's exactly an int)
			print("\t\tset_limit: Condition 1 hit")
		# Condition 2: starting spread is large, index outside of buffered top ask and top bid
		elif target_price < best_ask - spread_buffer_price/2:
			limit_ask_price = best_bid + spread_buffer_price/2
			print("\t\tset_limit: Condition 2a hit")
		elif target_price > best_bid + spread_buffer_price/2:
			limit_ask_price = best_ask - spread_buffer_price/2
			print("\t\tset_limit: Condition 2b hit")
		else:
			raise ValueError(f"ERROR - set_limit: Should never get here!!!!!")
		print("\t\tset_limit: limit_ask_price is ", limit_ask_price)

		buy_amt = price_to_ints(price=limit_ask_price, size=order_size, side=order_side)
		pay_amt = int(order_size * Decimal(10 ** base_erc20.decimal))
		price = (Decimal(buy_amt)/ Decimal(10**quote_erc20.decimal)) / (Decimal(pay_amt)/Decimal(10**base_erc20.decimal))



		print(f"\t\t book ask = {order_book_poller.book_best_ask.price}, book buy_amt = {order_book_poller.book_best_ask.quote_amt}, book pay_amt = {order_book_poller.book_best_ask.quote_amt}")
		print(f"\t\t my proposed ask = {price}, book buy_amt = {buy_amt}, book pay_amt = {pay_amt}")

		if price >= order_book_poller.book_best_ask.price:
			print("HUGE ERROR - set_limit: ask price generated is >= book best ask")
			print(f"HUGE ERROR cont. - set_limit: proposed price = {price}, book best price { order_book_poller.book_best_ask.price} ")
			return 
		
		if is_not_best:
			my_logger.not_best += 1
		else: 
			my_logger.no_offer += 1
		print(f"\t\tset_limit: Limit ask created for {base_allowance} WETH at price of: {limit_ask_price}")
		return {'pay_amt': pay_amt, 'pay_gem': list(token.poll_orderside().keys())[0], 'buy_amt': buy_amt, 'buy_gem': list(token.poll_orderside().keys())[1],
				 'order_side':order_side, 'price':price, 'size':order_size }
		
	elif order_side == OrderSide.BUY:
		# Condition 0: set_limit is true and best_ask is not going to get me arbed
		if set_closest and best_ask < market_price.price * bid_thresh_percent:
			limit_bid_price = best_ask
			print("\t\tset_limit: Condition 0 Hit")    
		# Condition 1: starting spread is large, index is within buffered top ask and bid
		#TODO: added "=" to all "<" and ">". See if this causes problems
		elif target_price <= best_ask - spread_buffer_price/2 and target_price >= best_bid + spread_buffer_price/2:
			limit_bid_price = target_price
			print("\t\tset_limit: Condition 1 Hit")
		# Condition 2: starting spread is large, index outside of buffered top ask and top bid
		elif target_price < best_ask - spread_buffer_price/2:
			limit_bid_price = best_bid + spread_buffer_price/2
			print("\t\tset_limit: Condition 2a Hit")
		elif target_price > best_bid + spread_buffer_price/2:
			limit_bid_price = best_ask - spread_buffer_price/2
			print("\t\tset_limit: Condition 2b hit")
		else:
			raise ValueError(f"ERROR - set_limit: Should never get here!!!!!")
		print("\t\tset_limit: Limit_bid_price is ", limit_bid_price)
		
		pay_amt = price_to_ints(price=limit_bid_price, size=order_size, side=order_side, set_closest=set_closest)
		buy_amt = int(order_size * Decimal(10 ** base_erc20.decimal))

		price = (Decimal(pay_amt) / Decimal(10**quote_erc20.decimal)) / (Decimal(buy_amt)/Decimal(10**base_erc20.decimal))
		print("\t\tpost rounding bid price = ", price)

		print(f"\t\t book bid = {order_book_poller.book_best_bid.price}, book buy_amt = {order_book_poller.book_best_bid.base_amt}, book pay_amt = {order_book_poller.book_best_bid.quote_amt}")
		print(f"\t\t my proposed bid = {price}, book buy_amt = {buy_amt}, book pay_amt = {pay_amt}")

		if price <= order_book_poller.book_best_bid.price:
			print("HUGE ERROR - set_limit: bid price generated is <= book best bid")
			print(f"HUGE ERROR cont. - set_limit: proposed price = {price}, book best price { order_book_poller.book_best_bid.price} ")
			return 

		if is_not_best:
			my_logger.not_best += 1
		else: 
			my_logger.no_offer += 1
		print(f"\t\tset_limit: Limit bid created for {base_allowance} WETH at price of: {limit_bid_price}")          
		return {'pay_amt': pay_amt, 'pay_gem': list(token.poll_orderside().keys())[1], 'buy_amt': buy_amt, 'buy_gem': list(token.poll_orderside().keys())[0],
				 'order_side':order_side , 'price':price, 'size':order_size}

# Main loop that triggers orders
def order_loop() -> None:

	# See what sides need updating
	sell_check = check_best(OrderSide.SELL, size=base_allowance)
	buy_check = check_best(OrderSide.BUY, size=base_allowance)

	candidate_orders = []
	if requires_action(sell_check) and buy_check == OrderComparison.BEST and args.min_spread:
		print("\t\torder_loop: using set_closest on ask")
		my_logger.best_offer += 1
		my_logger.set_limit += 1
		candidate_orders.append(set_limit(OrderSide.SELL, order_quality_status=sell_check, set_closest=True))
	elif sell_check == OrderComparison.BEST and requires_action(buy_check) and args.min_spread:
		print("\t\torder_loop: using set_closest on buy")
		my_logger.best_offer += 1
		my_logger.set_limit += 1
		candidate_orders.append(set_limit(OrderSide.BUY, order_quality_status=buy_check, set_closest=True))
	else:
		print("\t\torder_loop: placing order on both sides")
		candidate_orders.append(set_limit(OrderSide.BUY, order_quality_status=buy_check, set_closest=False))
		candidate_orders.append(set_limit(OrderSide.SELL, order_quality_status=sell_check, set_closest=False))

	# print(candidate_orders)
	offer_pay_amts = []
	offer_pay_gems = []
	offer_buy_amts = []
	offer_buy_gems = []

	for order in candidate_orders:
		if order is None:
			continue
		offer_pay_amts.append(order['pay_amt'])
		offer_pay_gems.append(Web3.to_checksum_address(order['pay_gem']))
		offer_buy_amts.append(order['buy_amt'])
		offer_buy_gems.append(Web3.to_checksum_address(order['buy_gem']))

	if len(offer_pay_amts) > 0:
		print("\t\torder_loop: starting offer...")
		if len(offer_pay_amts) == 2 and offer_buy_amts[1] <= offer_pay_amts[0]:
			print(f"HUGE ERROR - order_loop: buy_amts[1] ({offer_buy_amts[1]}) <= pay_amts[0] ({offer_pay_amts[0]})")
			print(f"HUGE ERROR cont. - order_loop: pay_amts[1] = {offer_pay_amts[1]} | buy_amts[0] = ({offer_buy_amts[0]})")
			return

		for i in range(len(offer_pay_amts)):
			try:
				print("\t\torder_loop: Placing Order.")
				transaction_result = client.market.offer(pay_amt=offer_pay_amts[i],
															pay_gem=offer_pay_gems[i],
															buy_amt=offer_buy_amts[i],
															buy_gem=offer_buy_gems[i])

				if transaction_result.status == 1:
					if token != TokenPairs.WETH_USDC_ARB:
						my_logger.offers_gas_fees.append(Decimal(str(transaction_result.l1_fee*(.1**gas_erc20.decimal))) * gas_price.price)
					print("\t\torder_loop: Offer Transaction Succeeded")
					my_logger.offer_placed += 1
				else:
					print("ERROR - order_loop: Offer Transaction Failed")
					my_logger.offer_fail += 1
					error_notifier.error_occured(transaction_result.transaction_hash, token)
				print(f"order_loop offer transaction result: {transaction_result}")
				print("\n\n\n ")

			except Exception as e:
				print(f"ERROR - order_loop: new offer error {e}")
				my_logger.offer_fail += 1
	else:
		print("\t\torder_loop: No new offer order was placed.")
	short_summary()
	
def short_summary() -> None:
	print(f"\t\tPrice of {token.sign_list()[0]}: {market_price.price}")
	if order_book_poller.book_best_ask.price and order_book_poller.book_best_bid.price:
		print(f"\t\tSpread: {order_book_poller.book_best_ask.price - order_book_poller.book_best_bid.price}")
	else:
		print(f"\t\tSpread: No data")
	
	my_best_ask = order_book_poller.my_best_ask.price if order_book_poller.my_best_ask else None
	my_best_bid = order_book_poller.my_best_bid.price if order_book_poller.my_best_bid else None

	print(f"\t\tOrderbook Best ask: {order_book_poller.book_best_ask.price} || My Best ask: {my_best_ask}")
	print(f"\t\tOrderbook Best bid: {order_book_poller.book_best_bid.price} || My Best bid: {my_best_bid}")

def long_summary() -> None:

	order_book_poller.poll_book()

	# Import data to logger object
	my_logger.wallet_value = uniswapper.calculate_wallet_value()
	my_logger.orders_value = order_book_poller.order_value
	my_logger.uniswapper_losses = uniswapper.swap_losses

	# Print Summary
	print(my_logger)

	# Write to logs
	if my_logger.times_printed % 1 == 0:
		with open(os.path.join("./logs",token.get_log_path()), 'a') as file:
			file.write(str(my_logger))
			file.write("\n\n\n")


@app.route('/')
def main() -> None:
	pass

if __name__ == '__main__':
	# Run the Flask app
	scheduler = BackgroundScheduler()

	print(f"Starting spread converted is {convert_spread_ints(start_spread_buffer,size=base_allowance)}")

	# Listen for events
	thread = threading.Thread(target=rubicon_listener, args=(my_queue,))
	thread.daemon = True  # Set the thread as a daemon so it doesn't block program termination
	thread.start()

	# updates price of eth
	update_market_price()
	scheduler.add_job(func=update_market_price, trigger="interval", seconds=16)

	# Wait for global variables to populate
	time.sleep(2)

	order_loop()
	scheduler.add_job(func=order_loop, trigger="interval", seconds=60*args.loop_time)

	# if args.cancel_all is not None:
	# 	scheduler.add_job(func=cancel_all, trigger="interval", seconds=60*args.cancel_all)

	if args.no_arb:
		scheduler.add_job(func=arb_checker, trigger="interval", seconds=15)

	long_summary()
	scheduler.add_job(func=long_summary, trigger="interval", seconds=60*30)

	scheduler.start()

	# Shut down the scheduler when exiting the app
	atexit.register(lambda: scheduler.shutdown())

	app.run(port=token_port)
