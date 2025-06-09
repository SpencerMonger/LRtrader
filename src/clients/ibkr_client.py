"""
The brokerage module handles communication with the Interactive Brokers TWS API.

    For more information on implementing the TWS API, please refer to their documentation here:
https://ibkrcampus.com/campus/ibkr-api-page/twsapi-doc/#api-introduction

For a step-by-step guide on installing the `ibapi` package, please review the detailed guide in
the Monger Notion wiki:
https://www.notion.so/IB-API-Source-Code-Setup-ba3636ee75c34e128250354612093405?pvs=4
"""

from ibapi.client import EClient

from .ibkr_wrapper import MongerWrapper


class MongerClient(EClient):
    """
    The MongerClient handles providing WRITE access to IBKR.

    Refer to the IBKR's Architecture Documentation for more information
    https://www.interactivebrokers.com/campus/ibkr-api-page/twsapi-doc/#architecture

    :param MongerWrapper wrapper: The `EWrapper` instance from IBKR's API.
    """

    def __init__(self, wrapper: MongerWrapper):
        """_summary_

        :param MongerWrapper wrapper: _description_
        """
        EClient.__init__(self, wrapper)
