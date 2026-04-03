from web3 import AsyncWeb3, AsyncHTTPProvider
from hr_monitor.config import config


def get_arb_w3() -> AsyncWeb3:
    return AsyncWeb3(AsyncHTTPProvider(config.ARB_RPC))


def get_bsc_w3() -> AsyncWeb3:
    return AsyncWeb3(AsyncHTTPProvider(config.BSC_RPC))


def get_base_w3() -> AsyncWeb3:
    return AsyncWeb3(AsyncHTTPProvider(config.BASE_RPC))
