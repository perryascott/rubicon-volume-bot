import time

from rubi import OrderSide
from _decimal import Decimal

class Logger:
    def __init__(self, token):
        self.token = token
        self.times_printed = 0

        self.set_limit = 0
        self.best_offer = 0
        self.insufficient_balance = []
        self.insufficient_gas = 0
        self.spread_small = 0
        self.rubi_api_error = 0
        self.price_api_error = 0 
        self.offer_placed = 0
        self.offer_fail = 0
        self.no_offer = 0
        self.not_best = 0
        self.cancel = []
        self.thresholds = 0

        self.uniswap_sides = []
        self.cancel_then_swaps = 0
        self.swap_error = 0

        self.insufficient_swaps_again = []
        self.insufficient_swaps = []


        self.cancel_prevented = 0
        self.cancel_failed = 0
        self.update_placed = 0
        self.update_fail = 0

        # Amount of eth (in dollars) paid on each transaction
        self.offers_gas_fees = []

        # Used to calculate % loss per order, total loss, 
        self.ask_my_price = []
        self.ask_volume = []
        self.ask_market_price = []
        self.bid_my_price = []
        self.bid_volume = []
        self.bid_market_price = []

        self.arb_cancel = 0

        # Uniswap trade data
        self.wallet_value = 0
        self.orders_value = 0
        self.uniswapper_losses = []

        # Uniswap price query data
        self.expected_uni_losses_taken = []
        self.expected_uni_losses_not_taken = []

        # Self takes
        self.self_takes = []

    def __str__(self):
        insuff_quote, insuff_base = parse_side(self.insufficient_balance)
        swap_insuff_ask, swap_insuff_bid = parse_side(self.insufficient_swaps)
        swap_insuff_ask_again, swap_insuff_bid_again = parse_side(self.insufficient_swaps_again)
        swap_ask, swap_bid = parse_side(self.uniswap_sides)
        cancel_ask, cancel_bid = parse_side(self.cancel)

        # Get gas spend stats
        total_gas_spent_rubi = sum(self.offers_gas_fees)
        total_gasses_rubi = len(self.offers_gas_fees)
        if total_gasses_rubi > 0:
            avg_gas_rubi = total_gas_spent_rubi/Decimal(str(total_gasses_rubi))
        else:
            avg_gas_rubi = 0

        # in dollars
        total_bid_volume = Decimal(0)
        for idx in range(len(self.bid_volume)):
            total_bid_volume += self.bid_volume[idx] * self.bid_market_price[idx]

        total_ask_volume = Decimal(0)
        for idx in range(len(self.ask_volume)):
            total_ask_volume += self.ask_volume[idx]* self.ask_market_price[idx]

        bid_arb = Decimal(0)
        for idx in range(len(self.bid_my_price)):
            bid_arb += (self.bid_my_price[idx] - self.bid_market_price[idx]) * self.bid_volume[idx]

        ask_arb = Decimal(0)
        for idx in range(len(self.ask_my_price)):
            ask_arb += (self.ask_market_price[idx] - self.ask_my_price[idx]) * self.ask_volume[idx]

        if total_bid_volume > Decimal('0'):
            bid_arb_per_vol = bid_arb / total_bid_volume
        else:
            bid_arb_per_vol = 0

        if total_ask_volume > Decimal('0'):
            ask_arb_per_vol = ask_arb / total_ask_volume
        else:
            ask_arb_per_vol = 0

        total_volume = total_bid_volume + total_ask_volume
        total_arb = bid_arb + ask_arb

        if total_volume > Decimal('0'):
            loss_per_vol = total_arb / total_volume
        else:
            loss_per_vol = 0

        # uniswap
        total_uni_loss = sum(self.uniswapper_losses)
        uni_loss_per_volume = Decimal("0") if total_volume == 0 else total_uni_loss/total_volume
        
        gas_per_volume = Decimal("0") if total_volume == 0 else total_gas_spent_rubi/total_volume

        losses_combined = total_uni_loss + total_arb + total_gas_spent_rubi
        losses_combined_per_volume = Decimal("0") if total_volume == 0 else losses_combined/total_volume
        
        # Self Takes
        total_self_taken = sum(self.self_takes)
        actual_total_volume = total_volume - total_self_taken
        actual_losses_combeined_per_volume = Decimal("0") if total_volume == 0 else losses_combined/actual_total_volume
        
        current_utc_time = time.time()
        cst_offset = -6 * 3600  # CST is UTC-6
        cst_time = current_utc_time + cst_offset

        cst_struct_time = time.gmtime(cst_time)
        nice_date_time = time.strftime("%A, %B %d, %Y %I:%M:%S %p", cst_struct_time)

        out = f"\n\t\t-- Summary: {nice_date_time} CST --\n"
        out += f"\t\tTotal times set_limit called: {self.set_limit} \n"
        out += f"\t\tTotal times NO action taken: {self.best_offer + insuff_quote + insuff_base + self.insufficient_gas + self.spread_small + self.rubi_api_error + self.price_api_error} \n"
        out += f"\t\t~ Best Offer: {self.best_offer} \n"
        out += f"\t\t~ Insufficiant quote: {insuff_quote} \n"
        out += f"\t\t~ Insufficiant base: {insuff_base} \n"
        out += f"\t\t~ Insufficiant gas: {self.insufficient_gas} \n"
        out += f"\t\t~ Spread too small: {self.spread_small} \n"
        out += f"\t\t~ Absurd Threshold Price: {self.thresholds} \n"
        out += f"\t\t~ Rubi API Error: {self.rubi_api_error} \n"
        out += f"\t\t~ Coinbase API Error: {self.price_api_error} \n"
        out += f"\t\tTotal times offer attempted: {self.no_offer+self.not_best} \n"
        out += f"\t\t~ No offer: {self.no_offer} \n"
        out += f"\t\t~ Not best: {self.not_best} \n"
        out += f"\t\tTotal times offer placed: {self.offer_placed} \n"
        out += f"\t\tTotal times offer failure occurred: {self.offer_fail} \n"
        out += f"\t\tTotal times cancel placed: {len(self.cancel)} \n"
        out += f"\t\t~ Asks: {cancel_ask} \n"
        out += f"\t\t~ Bids: {cancel_bid} \n"
        out += f"\t\tTotal times cancel prevented: {self.cancel_prevented} \n"
        out += f"\t\tTotal times cancel failed: {self.cancel_failed} \n"
        out += f"\t\tTotal times uniswap occurred: {swap_bid + swap_ask} \n"
        out += f"\t\t~ Asks: {swap_ask} \n"
        out += f"\t\t~ Bids: {swap_bid} \n"
        out += f"\t\tTotal times insufficient for uniswap: {swap_insuff_ask + swap_insuff_bid} \n"
        out += f"\t\t~ Asks: {swap_insuff_ask} \n"
        out += f"\t\t~ Bids: {swap_insuff_bid} \n"
        out += f"\t\tTotal times DOUBLE insufficient for uniswap: {swap_insuff_ask_again + swap_insuff_bid_again} \n"
        out += f"\t\t~ Asks: {swap_insuff_ask_again} \n"
        out += f"\t\t~ Bids: {swap_insuff_bid_again} \n"
        out += f"\t\tTotal times cancelled then uniswap occurred: {self.cancel_then_swaps} \n"
        out += f"\t\tTotal times swap error occured: {self.swap_error} \n\n"

        out += f"\t\tTotal volume: ${total_volume} \n"
        out += f"\t\tActual Total volume (minus self-takes): ${actual_total_volume} \n"
        out += f"\t\t~ Bid volume: ${total_bid_volume} \n"
        out += f"\t\t~ Ask volume: ${total_ask_volume} \n\n"

        out += f"\t\tTotal arb: ${total_arb} \n"
        out += f"\t\t~ Bid arb: ${bid_arb} \n"
        out += f"\t\t~ Ask arb: ${ask_arb} \n"
        out += f"\t\tArb per volume: ${loss_per_vol} \n"
        out += f"\t\t~ Bid arb per volume: ${bid_arb_per_vol} \n"
        out += f"\t\t~ Ask arb per volume: ${ask_arb_per_vol} \n"
        out += f"\t\t# of orders cancelled to avoid arb: {self.arb_cancel} \n\n"


        out += f"\t\tTotal Gas Spend: {total_gas_spent_rubi} \n"
        out += f"\t\t~ Over this many orders: {total_gasses_rubi} \n"        
        out += f"\t\t~ Avg Spend per Order: {avg_gas_rubi} \n"
        out += f"\t\t~ Avg Spend per Volume: {gas_per_volume} \n\n"

        out += f"\t\tTotal value of wallet: ${self.wallet_value+self.orders_value} \n"
        out += f"\t\t~ Wallet value: ${self.wallet_value} \n"
        out += f"\t\t~ Orders value: ${self.orders_value} \n\n"

        out += f"\t\tTotal value lost on Uniswap: ${total_uni_loss} \n"  
        out += f"\t\t~ Uniswap loss per volume: ${uni_loss_per_volume} \n"
        out += f"\t\tUsing uniswap fee of: {self.token.get_uniswap_fee()} \n"
        out += f"\t\tTotal number of uniswaps: {len(self.uniswapper_losses)} \n"
        out += f"\t\tTotal expected uniswap loss taken = ${sum(self.expected_uni_losses_taken)}\n"
        out += f"\t\tTotal expected uniswap loss NOT taken = ${sum(self.expected_uni_losses_not_taken)}\n"
        out += f"\t\tTotal # of orders not taken = {len(self.expected_uni_losses_not_taken)}\n\n"


        out += f"\t\tTotal value self-taken = ${total_self_taken}\n\n"


        out += f"\t\tTotal loss (gas, arb, and swap) = ${losses_combined}\n"
        out += f"\t\t~ Total loss (gas, arb, and swap) per volume = ${losses_combined_per_volume}\n"
        out += f"\t\t~ Actual Total loss (gas, arb, and swap) per volume (minus self take) = ${actual_losses_combeined_per_volume}\n"

        # TODO: add # of own orders eaten
        
        self.times_printed += 1
        return out


def parse_side(orders: list):
    ask = 0
    bid = 0
    for side in orders:
        if side == OrderSide.BUY:
            bid += 1
        elif side == OrderSide.SELL:
            ask += 1
        else:
            raise ValueError("logger unexpected side")
    return ask, bid