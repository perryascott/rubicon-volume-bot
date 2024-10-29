import requests, os
from multiprocessing import Queue
from dotenv import load_dotenv
from rubi import Client, EmitOfferEvent, EmitTakeEvent, EmitCancelEvent, EmitDeleteEvent
from decimal import Decimal
from events import TokenPairs
import time
import sys

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


class TokenPrice:
    def __init__(self, token: TokenPairs):
        self.price = None
        self.token = token
        match token:
            case TokenPairs.WETH_USDC:
                self.url = "https://api.coinbase.com/v2/prices/ETH-USD/spot"
            case TokenPairs.WETH_USDT:
                self.url = "https://api.coinbase.com/v2/prices/ETH-USD/spot"
            case TokenPairs.USDC_DAI:
                self.url_weth_usdc = "https://api.coinbase.com/v2/prices/ETH-USD/spot"
                self.url_weth_dai = "https://api.pro.coinbase.com/products/ETH-DAI/ticker"
            case TokenPairs.WETH_DAI:
                self.url = "https://api.pro.coinbase.com/products/ETH-DAI/ticker"
            case TokenPairs.OP_USDC:
                self.url = "https://api.coinbase.com/v2/prices/OP-USD/spot"
            case TokenPairs.WETH_USDC_ARB:
                self.url = "https://api.coinbase.com/v2/prices/ETH-USD/spot"

        # TODO: create an error if API fails more then X times in a row

    def update_price(self) -> None:
        if self.token == TokenPairs.USDC_DAI:
            response_usdc = requests.get(self.url_weth_usdc)
            response_dai = requests.get(self.url_weth_dai)

            if response_usdc.status_code == 200 and response_dai.status_code == 200:
                data_usdc = response_usdc.json()
                data_dai = response_dai.json()

                weth_dai_price = Decimal(str(data_dai['price']))
                weth_usdc_price = Decimal(str(data_usdc['data']['amount']))
                # print(f"usdc_dai price = {weth_dai_price/weth_usdc_price}")
                self.price = weth_dai_price/weth_usdc_price
            else:
                self.price = Decimal(1)
            return

        response = requests.get(self.url)
        data = response.json()

        if response.status_code == 200:
            if self.token == TokenPairs.WETH_DAI:
                coin_price = Decimal(str(data['price']))
            else:
                coin_price = Decimal(str(data['data']['amount']))
            self.price = coin_price

        else:
            print(f"\tERROR - update_price: Error occurred retrieving {self.token.sign()}  price")
            self.price = None

def get_client(queue: Queue, pair: TokenPairs) -> Client:

    # Read in environment information
    match pair:
        case TokenPairs.WETH_USDC:
            load_dotenv(".weth_usdc_env")
        case TokenPairs.WETH_USDT:
            load_dotenv(".weth_usdt_env")
        case TokenPairs.WETH_DAI:
            load_dotenv(".weth_dai_env")
        case TokenPairs.USDC_DAI:
            load_dotenv(".usdc_dai_env")
        case TokenPairs.OP_USDC:
            load_dotenv(".op_usdc_env")
        case TokenPairs.WETH_USDC_ARB:
            load_dotenv(".weth_usdc_arb_env")

    # Create Client
    print(os.getenv("HTTP_NODE_URL"))
    client = Client.from_http_node_url(
        http_node_url=os.getenv("HTTP_NODE_URL"),
        wallet= os.getenv("WALLET"),
        key=os.getenv("KEY"),
        message_queue=queue
    )

    # Add pair
    pair_string = pair.sign()
    allowance = pair.allowances()
    client.add_pair(
        pair_name=pair_string,
        base_asset_allowance=Decimal(allowance['base']),
        quote_asset_allowance=Decimal(allowance['quote'])
    )

    poll_time = .5

    # start listening to offer events created by your wallet on the WETH/USDC market and the WETH/USDC orderbook
    # client.start_event_poller(pair_string, event_type=EmitOfferEvent, poll_time=poll_time)
    client.start_event_poller(pair_string, event_type=EmitOfferEvent, filters={"maker": client.wallet}, poll_time=poll_time)
    client.start_event_poller(pair_string, event_type=EmitTakeEvent, filters={"maker": client.wallet}, poll_time=poll_time)
    # client.start_event_poller(pair_string, event_type=EmitCancelEvent, filters={"maker": client.wallet}, poll_time=poll_time)
    # client.start_event_poller(pair_string, event_type=EmitDeleteEvent, filters={"maker": client.wallet}, poll_time=poll_time)
    client.start_event_poller(pair_string, event_type=EmitCancelEvent, poll_time=poll_time)
    client.start_event_poller(pair_string, event_type=EmitDeleteEvent, poll_time=poll_time)

    return client


class BalanceNotification():
    def __init__(self, wait_time):
        self.last_notification_time = 0
        self.min_wait = 60*wait_time # seconds

    def send_notification(self,subject: str, message: str) -> None:
        if self.last_notification_time + self.min_wait > time.time():
            print("\t\tNotification.send_notification() - suppressing email")
            return
        
        # Email server settings
        smtp_server = 'smtp.gmail.com'
        sender_email = 'rubiscriptlistener@gmail.com'  # Replace with your email address
        sender_password = os.getenv("EMAIL_PASS")  # Replace with your email password

        # Recipient email address
        recipients = ['rubiscriptlistener@gmail.com']  # Replace with the recipient's email address

        msg = MIMEText(message)
        msg['Subject'] = subject
        msg['From'] = sender_email
        msg['To'] = ', '.join(recipients)
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp_server:
            smtp_server.login(sender_email, sender_password)
            smtp_server.sendmail(sender_email, recipients, msg.as_string())
        print("\t\tBalance Notification sent")
        self.last_notification_time = time.time()

class ErrorNotification():
    def __init__(self):
        self.last_notification_time = 0
        self.total_errors = []
        self.min_wait = 1 # seconds
        self.max_errors = 10

    def error_occured(self, hash, token) -> None:
        self.total_errors.append(time.time())
        if len(self.total_errors) >= self.max_errors:
            print("ERROR - ErrorNotification: Max number of errors reached, shutting down")
            subject = f"MAX ERRORS in {token.sign()} account."
            message = f"Hash is {hash} \nShutting down"
            self.send_notification(subject=subject, message=message,final=True)
            sys.exit(0)
        else: 
            subject = f"TRANSACTION ERROR in {token.sign()} account."
            message = f"Hash is {hash} \nTotal errors is {len(self.total_errors)}"
            self.send_notification(subject=subject, message=message)

    def send_notification(self,subject: str, message: str, final=False) -> None:
        if self.last_notification_time + self.min_wait > time.time() and not final:
            print("\t\tNotification.send_notification() - suppressing email")
            return
        
        # Email server settings
        smtp_server = 'smtp.gmail.com'
        sender_email = 'rubiscriptlistener@gmail.com'  # Replace with your email address
        sender_password = os.getenv("EMAIL_PASS")  # Replace with your email password

        # Recipient email address
        recipients = ['rubiscriptlistener@gmail.com']  # Replace with the recipient's email address

        msg = MIMEText(message)
        msg['Subject'] = subject
        msg['From'] = sender_email
        msg['To'] = ', '.join(recipients)
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp_server:
            smtp_server.login(sender_email, sender_password)
            smtp_server.sendmail(sender_email, recipients, msg.as_string())
        print("\t\tError notification sent")
        self.last_notification_time = time.time()

