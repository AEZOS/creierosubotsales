import aiohttp
import asyncio
import logging
from config import TATUM_API_KEY

async def check_ltc_transaction(address: str, amount_expected: float, timestamp_since: int) -> tuple[bool, int, str]:
    """
    Checks the Tatum API for a transaction to `address` matching `amount_expected`.
    Returns (True, confirmations, tx_hash) if found, or (False, 0, "") otherwise.
    """
    url = f"https://api.tatum.io/v3/litecoin/transaction/address/{address}?pageSize=10"
    
    headers = {"x-api-key": TATUM_API_KEY}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status != 200:
                    logging.error(f"Tatum API error: {response.status}")
                    return False, 0, ""
                
                data = await response.json()
                for tx in data:
                    # Filter by time
                    tx_time = tx.get("time", 0)
                    if tx_time < timestamp_since:
                        continue
                        
                    outputs = tx.get("outputs", [])
                    for out in outputs:
                        if out.get("address") == address:
                            val = float(out.get("value", "0"))
                            # Precision matching for multiplexed addresses
                            if abs(val - amount_expected) < 0.00005: 

                                confirmations = tx.get("confirmations", 0)
                                tx_hash = tx.get("hash", "")
                                return True, confirmations, tx_hash
                            
    except Exception as e:
        logging.exception(f"Exception checking Tatum API: {e}")
        
    return False, 0, ""
