from rubi import OrderSide, ERC20
import requests
import json
import time, os
from decimal import Decimal

from pairs import TokenPairs

# Poll rubicon orderbook and find my best offers and market's best.
class OrderBookRequester:
    def __init__(self, client, token : TokenPairs):
        self.client = client
        self.token = token

        # Aribtrum case
        if token==TokenPairs.WETH_USDC_ARB:
            self.url = "https://api.rubicon.finance/subgraphs/name/RubiconV2_Arbitrum_One"
        else:
            self.url = "https://api.rubicon.finance/subgraphs/name/RubiconV2_Optimism_Mainnet"
            
        # Track my best and orderbook best bids/ask
        self.my_best_bid = None
        self.my_best_ask = None
        self.all_my_bids = []
        self.all_my_asks = []
        self.book_best_bid = None
        self.book_best_ask = None
        self.last_poll_time = 0

        # Create ERC20 to get decimal and calculate price
        self.base_erc20 = ERC20.from_network(self.token.sign_list()[0], network=self.client.network)
        self.quote_erc20 = ERC20.from_network(self.token.sign_list()[1], network=self.client.network)
        self.asset = list(self.token.poll_orderside().keys())[0]
        self.quote = list(self.token.poll_orderside().keys())[1]

        # Store total value of existing orders
        self.order_value = None

    def poll_book(self) -> bool:

        headers = {'Content-Type': 'application/json'}

        query = f"""
        {{
        asks: offers(
            first: 1000
            orderBy: price
            orderDirection: desc
            where: {{pay_gem: "{self.asset}", buy_gem: "{self.quote}", open: true}}
        ) {{
            id
            pay_gem
            buy_gem
            pay_amt
            buy_amt
            paid_amt
            bought_amt
            price
            maker {{ id }}
        }}
        bids: offers(
            first: 1000
            orderBy: price
            orderDirection: desc
            where: {{pay_gem: "{self.quote}", buy_gem: "{self.asset}", open: true}}
        ) {{
            id
            pay_gem
            buy_gem
            pay_amt
            buy_amt
            paid_amt
            bought_amt
            price
            maker {{ id }}
        }}
        }}
        """

        headers = {'Content-Type': 'application/json'}
        response = requests.post(self.url, headers=headers, data=json.dumps({'query': query}))

        if response.status_code != 200:
            print("WARNING - OrderBookRequest.poll_book: JSON query failed.")
            return False
    
        data = response.json()
        # print(data)
        asks = data['data']['asks']
        bids = data['data']['bids']

        self.book_best_ask = None
        self.my_best_ask = None
        self.all_my_asks = []

        for ask in asks:
            price = (Decimal(ask['buy_amt']) / Decimal(10**self.quote_erc20.decimal)) / (Decimal(ask['pay_amt'])/Decimal(10**self.base_erc20.decimal))
            is_book_best_ask = False
            # calculate best price and add to self.best_ask
            if self.book_best_ask is None or self.book_best_ask.price > price:
                self.book_best_ask = PolledOrder(limit_order_id=ask['id'],
                                           order_side=OrderSide.SELL,
                                           price=price,
                                           base_gem=ask['pay_gem'],
                                           base_amt=ask['pay_amt'],
                                           quote_gem=ask['buy_gem'],
                                           quote_amt=ask['buy_amt'],
                                           bought_amt=ask['bought_amt'],
                                           paid_amt=ask['paid_amt'],
                                           wallet_id=ask['maker']['id'])
                is_book_best_ask = True

            if ask['maker']['id'] == os.getenv("WALLET").lower():
                my_order = PolledOrder(limit_order_id=ask['id'],
                                                order_side=OrderSide.SELL,
                                                price=price,
                                                base_gem=ask['pay_gem'],
                                                base_amt=ask['pay_amt'],
                                                quote_gem=ask['buy_gem'],
                                                quote_amt=ask['buy_amt'],
                                                bought_amt=ask['bought_amt'],
                                                paid_amt=ask['paid_amt'],
                                                wallet_id=ask['maker']['id'])
                # print(ask['id'])
                self.all_my_asks.append(my_order)
                if (self.my_best_ask is None or self.my_best_ask.price > price):
                    if is_book_best_ask:
                        self.my_best_ask = self.book_best_ask
                    else:
                        self.my_best_ask = my_order

        self.my_best_bid = None
        self.book_best_bid = None
        self.all_my_bids = []

        for bid in bids:
            price = (Decimal(bid['pay_amt']) / Decimal(10**self.quote_erc20.decimal)) / (Decimal(bid['buy_amt'])/Decimal(10**self.base_erc20.decimal))

            # calculate best price and add to self.best_bid
            is_book_best_bid = False
            if self.book_best_bid is None or self.book_best_bid.price < price:
                self.book_best_bid = PolledOrder(limit_order_id=bid['id'],
                                           order_side=OrderSide.BUY,
                                           price=price,
                                           base_gem=bid['buy_gem'],
                                           base_amt=bid['buy_amt'],
                                           quote_gem=bid['pay_gem'],
                                           quote_amt=bid['pay_amt'],
                                           bought_amt=bid['bought_amt'],
                                           paid_amt=bid['paid_amt'],
                                           wallet_id=bid['maker']['id'])
                is_book_best_bid = True

            if bid['maker']['id'] == os.getenv("WALLET").lower():
                my_order = PolledOrder(limit_order_id=bid['id'],
                                                    order_side=OrderSide.SELL,
                                                    price=price,
                                                    base_gem=bid['buy_gem'],
                                                    base_amt=bid['buy_amt'],
                                                    quote_gem=bid['pay_gem'],
                                                    quote_amt=bid['pay_amt'],
                                                    bought_amt=bid['bought_amt'],
                                                    paid_amt=bid['paid_amt'],
                                                    wallet_id=bid['maker']['id'])
                self.all_my_bids.append(my_order)
                if (self.my_best_bid is None or self.my_best_bid.price < price):
                    if is_book_best_bid:
                        self.my_best_bid = self.book_best_bid
                    else:
                        self.my_best_bid = my_order

        # On the off chance there are no orders on that side
        if self.book_best_bid is None:
            self.book_best_bid = PolledOrder.get_empty()
            self.book_best_bid.price = Decimal('0')
        if self.book_best_ask is None:
            self.book_best_ask = PolledOrder.get_empty()
            self.book_best_ask.price = Decimal('100000')
            
        # Get value of current existing orders
        value = 0
        for my_ask in self.all_my_asks:
            # print(f"my_ask.quote_amt = {my_1ask.quote_amt}, my_ask.bought_amt = {my_ask.bought_amt}")
            value += (Decimal(my_ask.quote_amt)-Decimal(my_ask.bought_amt)) / Decimal(10**self.quote_erc20.decimal)
        for my_bid in self.all_my_bids:
            # print(f"my_bid.quote_amt = {my_bid.quote_amt}, my_bid.bought_amt = {my_bid.bought_amt}")
            value += (Decimal(my_bid.quote_amt)-Decimal(my_bid.paid_amt))  / Decimal(10**self.quote_erc20.decimal)
        self.order_value = value

        self.last_poll_time = time.time()
        return True

    def is_poll_recent(self, allowable_time=10) -> bool:
        return time.time() < self.last_poll_time + allowable_time

    # def remove_order(self, limit_order_id):
    #     idx = 0
    #     for my_ask in self.all_my_asks:
    #         if my_ask.limit_order_id == limit_order_id:
    #             self.all_my_asks.pop(idx)
    #             return
    #         idx += 1
    #     idx = 0
    #     for my_bid in self.all_my_bids:
    #         if my_bid.limit_order_id == limit_order_id:
    #             self.all_my_bids.pop(idx)
    #             return
    #         idx += 1


# Used to hold data from Orderbook poll
class PolledOrder:
    def __init__(self, 
                 limit_order_id, 
                 order_side,
                 price,
                 base_gem, 
                 base_amt, 
                 quote_gem,
                 quote_amt,
                 bought_amt,
                 paid_amt,
                 wallet_id):
        
        self.limit_order_id = limit_order_id
        self.order_side = order_side
        self.price = price
        self.base_gem = base_gem
        self.base_amt = base_amt
        self.quote_gem = quote_gem
        self.quote_amt = quote_amt
        self.bought_amt = bought_amt
        self.paid_amt = paid_amt
        self.wallet_id = wallet_id
    
    def get_empty():
        return PolledOrder(None, None, None, None, None, None, None, None, None, None)

# Holds time of last cancel
class LastCancelTimes:
    def __init__(self, min_wait_time):
        self.last_bid_cancel = 0
        self.last_ask_cancel = 0
        self.min_wait_time = min_wait_time * 60 if min_wait_time else 0
    
    def can_cancel(self, order_side: OrderSide) -> bool:
        if order_side == OrderSide.BUY:
            if self.last_bid_cancel + self.min_wait_time < time.time():
                self.last_bid_cancel = time.time()
                return True
        elif order_side == OrderSide.SELL:
            if self.last_ask_cancel + self.min_wait_time < time.time():
                self.last_ask_cancel = time.time()
                return True
        else:
            raise ValueError("LastCancelTimes.can_cancel: bad order_side value")
        return False