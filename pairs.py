from rubi import OrderSide
from enum import Enum
from decimal import Decimal
from web3 import Web3

# optimism
weth = "0x4200000000000000000000000000000000000006"
usdc = "0x7f5c764cbc14f9669b88837ca1490cca17c31607"
usdt = "0x94b008aa00579c1307b0ef2c499ad98a8ce58e58"
dai = "0xda10009cbd5d07dd0cecc66161fc93d7c9000da1"
op = "0x4200000000000000000000000000000000000042"

# arbitrum
weth_arb = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
usdc_arb = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"

class BestPrices:
    def __init__(self):
        self.best_ask = None
        self.best_bid = None
    
class OrderComparison(Enum):
    """Enumeration representing an order comparison."""
    NO_ORDERS = "NO_ORDERS"
    BEST = "BEST"
    NOT_BEST = "NOT_BEST"
    ERROR_RETRIEVING = "ERROR_RETRIEVING"
    THRESHOLD_PRICES = "THRESHOLD_PRICES"

class TokenPairs(Enum):
    """Enumeration representing an order comparison."""
    WETH_USDC = "WETH_USDC"
    WETH_USDT = "WETH_USDT"
    USDC_DAI = "USDC_DAI"
    WETH_DAI = "WETH_DAI"
    OP_USDC = "OP_USDC"
    WETH_USDC_ARB = "WETH_USDC_ARB"

    def sign(self) -> str:
        match self:
            case TokenPairs.WETH_USDC:
                return "WETH/USDC"
            case TokenPairs.WETH_USDT:
                return "WETH/USDT"
            case TokenPairs.USDC_DAI:
                return "USDC/DAI"
            case TokenPairs.WETH_DAI:
                return "WETH/DAI"
            case TokenPairs.OP_USDC:
                return "OP/USDC"
            case TokenPairs.WETH_USDC_ARB:
                return "WETH/USDC"
            
    def sign_list(self) -> list[str]:
        match self:
            case TokenPairs.WETH_USDC:
                return ["WETH","USDC"]
            case TokenPairs.WETH_USDT:
                return ["WETH","USDT"]
            case TokenPairs.USDC_DAI:
                return ["USDC","DAI"]
            case TokenPairs.WETH_DAI:
                return ["WETH","DAI"]
            case TokenPairs.OP_USDC:
                return ["OP","USDC"]
            case TokenPairs.WETH_USDC_ARB:
                return ["WETH","USDC"]
            
    def allowances(self):
        match self:
            case TokenPairs.WETH_USDC:
                return {"base":1000, "quote":2000000}
            case TokenPairs.WETH_USDT:
                return {"base":1000, "quote":2000000}
            case TokenPairs.USDC_DAI:
                return {"base":2000000, "quote":2000000}
            case TokenPairs.WETH_DAI:
                return {"base":1000, "quote":200000}
            case TokenPairs.OP_USDC:
                return {"base":1400000, "quote":2000000}
            case TokenPairs.WETH_USDC_ARB:
                return {"base":100000, "quote":200000000}
            
    def target_allowances(self):
        match self:
            case TokenPairs.WETH_USDC:
                return Decimal("0.025")
            case TokenPairs.WETH_USDT:
                return Decimal("0.05")
            case TokenPairs.USDC_DAI:
                return Decimal("1000")
            case TokenPairs.WETH_DAI:
                return Decimal("0.05")
            case TokenPairs.OP_USDC:
                return Decimal("75")
            case TokenPairs.WETH_USDC_ARB:
                return Decimal("0.17 ")
            
    # Rubicon maximum arbitrage allowed
    def alpha(self):
        match self:
            case TokenPairs.WETH_USDC:
                return Decimal("0.007")
            case TokenPairs.WETH_USDT:
                return Decimal("0.0007")
            case TokenPairs.USDC_DAI:
                return Decimal("0.007")
            case TokenPairs.WETH_DAI:
                return Decimal("0.0007")
            case TokenPairs.OP_USDC:
                return Decimal("0.0007")
            case TokenPairs.WETH_USDC_ARB:
                return Decimal("0.012")
    
    # Rubicon percentage that "tack" argument operates at
    # Must be less than alpha
    def gamma(self):
        match self:
            case TokenPairs.WETH_USDC:
                return Decimal("0.005")
            case TokenPairs.WETH_USDT:
                return Decimal("0")
            case TokenPairs.USDC_DAI:
                return Decimal("0.005")
            case TokenPairs.WETH_DAI:
                return Decimal("0.0007")
            case TokenPairs.OP_USDC:
                return Decimal("0.0007")
            case TokenPairs.WETH_USDC_ARB:
                return Decimal("0.012")
            
    # Uniswap max arbitrage allowed
    def beta(self):
        match self:
            case TokenPairs.WETH_USDC:
                return Decimal('0.0007')
            case TokenPairs.WETH_USDT:
                return Decimal('0.0006')
            case TokenPairs.USDC_DAI:
                return Decimal('0.0005')
            case TokenPairs.WETH_DAI:
                return Decimal('0.0006')
            case TokenPairs.OP_USDC:
                return Decimal('0.0006')
            case TokenPairs.WETH_USDC_ARB:
                return Decimal('0.005')

    def poll_orderside(self):
        match self:
            case TokenPairs.WETH_USDC:
                return {weth: OrderSide.BUY, usdc: OrderSide.SELL }
            case TokenPairs.WETH_USDT:
                return {weth: OrderSide.BUY, usdt: OrderSide.SELL }
            case TokenPairs.USDC_DAI:
                return {usdc: OrderSide.BUY, dai: OrderSide.SELL }
            case TokenPairs.WETH_DAI:
                return {weth: OrderSide.BUY, dai: OrderSide.SELL }
            case TokenPairs.OP_USDC:
                return {op: OrderSide.BUY, usdc: OrderSide.SELL }
            case TokenPairs.WETH_USDC_ARB:
                return {weth_arb: OrderSide.BUY, usdc_arb: OrderSide.SELL }

    def get_checksum_addresses(self):
        match self:
            case TokenPairs.WETH_USDC:
                return [Web3.to_checksum_address(weth), Web3.to_checksum_address(usdc)]
            case TokenPairs.WETH_USDT:
                return [Web3.to_checksum_address(weth), Web3.to_checksum_address(usdt)]
            case TokenPairs.USDC_DAI:
                return [Web3.to_checksum_address(usdc), Web3.to_checksum_address(dai)]
            case TokenPairs.WETH_DAI:
                return [Web3.to_checksum_address(weth), Web3.to_checksum_address(dai)]
            case TokenPairs.OP_USDC:
                return [Web3.to_checksum_address(op), Web3.to_checksum_address(usdc)]
            case TokenPairs.WETH_USDC_ARB:
                return [Web3.to_checksum_address(weth_arb), Web3.to_checksum_address(usdc_arb)]
            
    def get_log_path(self):
        match self:
            case TokenPairs.WETH_USDC:
                return "weth_usdc.txt"
            case TokenPairs.WETH_USDT:
                return "weth_usdt.txt"
            case TokenPairs.USDC_DAI:
                return "usdc_dai.txt"
            case TokenPairs.WETH_DAI:
                return "weth_dai.txt"
            case TokenPairs.OP_USDC:
                return "op_usdc.txt"
            case TokenPairs.WETH_USDC_ARB:
                return "weth_usdc_arb.txt"

    def get_uniswap_fee(self):
        match self:
            case TokenPairs.WETH_USDC:
                return 500
            case TokenPairs.WETH_USDT:
                return 0
            case TokenPairs.USDC_DAI:
                return 100
            case TokenPairs.WETH_DAI:
                return 0
            case TokenPairs.OP_USDC:
                return 0
            case TokenPairs.WETH_USDC_ARB:
                return 500
