import os, requests, time
from uniswap import Uniswap
from _decimal import Decimal
from rubi import ERC20, OrderSide
from hexbytes import HexBytes
from events import TokenPairs
from utils import TokenPrice
from transactionLogging import Logger

class Uniswapper:
    def __init__(self, pair: TokenPairs,
                    quoteERC20: ERC20, 
                    baseERC20: ERC20, 
                    gasERC20: ERC20, 
                    market_price: TokenPrice, 
                    gas_price: TokenPrice, 
                    beta: Decimal,
                    logger: Logger):
        
        self.pair = pair
        self.quoteERC20 = quoteERC20
        self.baseERC20 = baseERC20
        self.gasERC20 = gasERC20
        self.market_price = market_price
        self.gas_price = gas_price

        address = os.getenv("WALLET")         # or None if you're not going to make transactions
        private_key = os.getenv("KEY") # or None if you're not going to make transactions
        version = 3              # specify which version of Uniswap to use
        provider = os.getenv("HTTP_NODE_URL")    # can also be set through the environment variable `PROVIDER`

        self.uniswap = Uniswap(address=address, private_key=private_key, version=version, provider=provider)

        self.swap_gas = []
        self.swap_price = []
        self.swap_amt = []

        # Hold the loss on each
        self.swap_losses = []
        # TODO: 2. use hex to get gas spend and value of trade for uniswap
        # track uniswap gas spend here
        # market price at time of trade and trade value, should be able to track % loss per swap
        # and the amount per swap (in units of quote)

        # Acceptable loss on swap fraction
        self.beta = beta

        # Logger object
        self.logger = logger

    def swap(self, side: OrderSide, trade_amt: int, base_allowance: Decimal, set_closest: bool, next_fee: bool = False) -> int:
        # Where side is the trade that I'm trying to make on the rubicon end, so this
        # will be the opposite. I.e. I want to make a bid (buy weth with usdc), but I'm out of USDC,
        # so need to trade WETH for USDC first.
        base = self.pair.get_checksum_addresses()[0]
        quote = self.pair.get_checksum_addresses()[1]

        pre_value = self.calculate_wallet_value()

        # Trying to buy base w/ quote on rubicon, but not enough quote
        # trade base I have for quote on uniswap
        if side == OrderSide.BUY:
            try:
                # Amount for a full trade
                amt = int(base_allowance * Decimal(10 ** self.baseERC20.decimal) * Decimal("1.05")) # plus 5 %
                amt_check = trade_amt if set_closest else amt + trade_amt
                print(f"{side} | amt = {amt} | amt_check = {amt_check} | trade_amt = {trade_amt}")
                base_balance = self.baseERC20.balance_of(account=os.getenv("WALLET"))
                if base_balance >= amt_check:
                    
                    ### Check Price
                    min_swap_price = (Decimal('1') - self.beta) * self.market_price.price
                    # quote
                    max_output = self.uniswap.get_price_input(base, quote, qty=trade_amt,fee=self.pair.get_uniswap_fee())
                    # base
                    readable_amt = (trade_amt / Decimal(10**self.baseERC20.decimal))
                    # quote
                    readable_max_output = (Decimal(str(max_output))) / Decimal(10**self.quoteERC20.decimal)
                    swap_price = readable_max_output/readable_amt

                    expected_loss = self.market_price.price * readable_amt - readable_max_output
                    # print(f"{side} - swap_price is {swap_price}, min_swap_price is {min_swap_price}")
                    # print(f"{side} - expected loss is {expected_loss}")
                    if swap_price < min_swap_price:
                        self.logger.expected_uni_losses_not_taken.append(expected_loss)
                        print(f"\t\tswap: bad price - swap on {side} for price {swap_price}")
                        return -2
                    else:
                        self.logger.expected_uni_losses_taken.append(expected_loss)

                    # Make Swap
                    hex = self.uniswap.make_trade(base , quote, qty=trade_amt,fee=self.pair.get_uniswap_fee())
                    print(f"swap: Uniswap result hex = {hex.hex}")
                    time.sleep(10)
                    post_value = self.calculate_wallet_value()
                    print(f"\t\tswap: Value lost on uniswap = {pre_value - post_value}")
                    self.swap_losses.append(pre_value - post_value)
                    return 1
                else:
                    print(f"swap: Not enough funds to execute swap on {side}. amt = {amt}")
                    return 0
            except Exception as e:
                print(f"ERROR - swap: traceback")
                print(e)
                return -1
        
        # Trying to buy base w/ quote on rubicon, but not enough quote
        # trade base I have for quote on uniswap
        elif side == OrderSide.SELL:
            try:
                amt = int(self.market_price.price * base_allowance * Decimal(10 ** self.quoteERC20.decimal) * Decimal("1.05"))
                amt_check = trade_amt if set_closest else amt + trade_amt
                print(f"{side} | amt = {amt} | amt_check = {amt_check} | trade_amt = {trade_amt}")
                quote_balance = self.quoteERC20.balance_of(account=os.getenv("WALLET"))
                if quote_balance >= amt_check:

                    ## Check Price
                    max_swap_price = (Decimal('1') + self.beta) * self.market_price.price
                    # base
                    max_output = self.uniswap.get_price_input(quote, base, qty=trade_amt,fee=self.pair.get_uniswap_fee())
                    # quote
                    readable_amt = (trade_amt / Decimal(10**self.quoteERC20.decimal))
                    # base
                    readable_max_output = (Decimal(str(max_output))) / Decimal(10**self.baseERC20.decimal)
                    swap_price = readable_amt/readable_max_output
                    expected_loss = readable_amt-readable_max_output * self.market_price.price
                    # print(f"{side} - swap_price is {swap_price}, max_swap_price is {max_swap_price}")
                    # print(f"{side} - expected loss is {expected_loss}")
                    if swap_price > max_swap_price:
                        self.logger.expected_uni_losses_not_taken.append(expected_loss)
                        print(f"\t\tswap: bad price - swap on {side} for price {swap_price}")
                        return -2
                    else:
                        self.logger.expected_uni_losses_taken.append(expected_loss)


                    # Make Swap
                    hex = self.uniswap.make_trade(quote, base, qty=trade_amt,fee=self.pair.get_uniswap_fee())
                    print(f"swap: Uniswap result hex = {hex.hex}")
                    time.sleep(10)
                    post_value = self.calculate_wallet_value()
                    print(f"\t\tswap: Value lost on uniswap = {pre_value - post_value}")
                    self.swap_losses.append(pre_value - post_value)
                    return 1
                else:
                    print(f"swap: Not enough funds to execute swap on {side}. amt = {amt}")
                    return 0
            except Exception as e:
                print(f"ERROR - swap: traceback")
                print(e)
                return -1
    
    # Calculates value of wallet
    def calculate_wallet_value(self) -> Decimal:
        base_value = self.baseERC20.to_decimal(number=self.baseERC20.balance_of(account=os.getenv("WALLET")))*self.market_price.price
        quote_value = self.quoteERC20.to_decimal(number=self.quoteERC20.balance_of(account=os.getenv("WALLET")))
        if self.pair == TokenPairs.WETH_USDC_ARB:
            gas_value = 0
        else:
            gas_value = self.gasERC20.to_decimal(number=self.gasERC20.balance_of(account=os.getenv("WALLET")))*self.gas_price.price
        return base_value + quote_value + gas_value


# TODO: 2. use hex to get gas spend and value of trade for uniswap
# This currently isn't used and does nothing
class HashGetter:
    def __init__(self):
        self.api_key = '3RS4PV5Z66QA828RNBMPE21XMY88FD3Q1Z'

    def get_tx_reciept(self, hash: HexBytes):
        url = f"""https://api-optimistic.etherscan.io/api
                ?module=transaction
                &action=getstatus
                &txhash={hash.hex}
                &apikey={self.api_key}"""
        url = f"""https://api-optimistic.etherscan.io/api
                ?module=account
                &action=txlist
                &address=0xE799f28268d0e34a30E8D98a5084F2A265395315
                &startblock=0
                &endblock=99999999
                &page=1
                &offset=10
                &sort=asc
                &apikey=3RS4PV5Z66QA828RNBMPE21XMY88FD3Q1Z"""
        response = requests.get(url)
        data = response.json()
        print(data)