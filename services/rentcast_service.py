"""
RentCast API Service for Property Intelligence Data

Provides property data lookups including:
- Sale history and price trends
- Property details (beds, baths, sqft, etc.)
- Tax assessment history
- Owner information
- Property features

Usage:
    from services.rentcast_service import fetch_property_data
    
    result = fetch_property_data("123 Main St", "San Antonio", "TX", "78244")
    if result['success']:
        data = result['data']
    else:
        error = result['error']
"""

import requests
import logging
from urllib.parse import quote
from config import Config

logger = logging.getLogger(__name__)

RENTCAST_BASE_URL = "https://api.rentcast.io/v1"


def fetch_property_data(street_address: str, city: str, state: str, zip_code: str) -> dict:
    """
    Fetch property data from RentCast API.
    
    Args:
        street_address: Street address (e.g., "5500 Grand Lake Dr")
        city: City name (e.g., "San Antonio")
        state: State code (e.g., "TX")
        zip_code: ZIP code (e.g., "78244")
    
    Returns:
        dict with keys:
            - success (bool): Whether the request succeeded
            - data (dict): Property data if successful
            - error (str): Error message if failed
    """
    api_key = Config.RENTCAST_API_KEY
    
    if not api_key:
        logger.error("RENTCAST_API_KEY not configured")
        return {
            'success': False,
            'error': 'RentCast API key not configured. Please contact your administrator.'
        }
    
    # Build full address string
    address_parts = [street_address]
    if city:
        address_parts.append(city)
    if state:
        address_parts.append(state)
    if zip_code:
        address_parts.append(zip_code)
    
    full_address = ", ".join(address_parts)
    
    if not street_address:
        return {
            'success': False,
            'error': 'Street address is required for property lookup.'
        }
    
    try:
        # Make API request
        url = f"{RENTCAST_BASE_URL}/properties"
        headers = {
            'Accept': 'application/json',
            'X-Api-Key': api_key
        }
        params = {
            'address': full_address
        }
        
        logger.info(f"Fetching RentCast data for: {full_address}")
        
        response = requests.get(url, headers=headers, params=params, timeout=30)
        
        # Handle response codes
        if response.status_code == 200:
            data = response.json()
            
            # RentCast returns an array of properties - take the first match
            if isinstance(data, list) and len(data) > 0:
                property_data = data[0]
                logger.info(f"Successfully fetched property data for: {full_address}")
                return {
                    'success': True,
                    'data': property_data,
                    'address_used': full_address
                }
            elif isinstance(data, dict) and data:
                # Sometimes returns single object
                logger.info(f"Successfully fetched property data for: {full_address}")
                return {
                    'success': True,
                    'data': data,
                    'address_used': full_address
                }
            else:
                logger.warning(f"No property data found for: {full_address}")
                return {
                    'success': False,
                    'error': 'No property data found for this address. Please verify the address is correct.'
                }
        
        elif response.status_code == 401:
            logger.error("RentCast API authentication failed")
            return {
                'success': False,
                'error': 'API authentication failed. Please check your API key.'
            }
        
        elif response.status_code == 429:
            logger.warning("RentCast API rate limit exceeded")
            return {
                'success': False,
                'error': 'API rate limit exceeded. Please try again later.'
            }
        
        elif response.status_code == 404:
            logger.info(f"No property found at: {full_address}")
            return {
                'success': False,
                'error': 'No property data found for this address.'
            }
        
        else:
            logger.error(f"RentCast API error: {response.status_code} - {response.text}")
            return {
                'success': False,
                'error': f'API error (status {response.status_code}). Please try again later.'
            }
    
    except requests.exceptions.Timeout:
        logger.error("RentCast API request timed out")
        return {
            'success': False,
            'error': 'Request timed out. Please try again.'
        }
    
    except requests.exceptions.ConnectionError:
        logger.error("Failed to connect to RentCast API")
        return {
            'success': False,
            'error': 'Unable to connect to property data service. Please check your internet connection.'
        }
    
    except Exception as e:
        logger.exception(f"Unexpected error fetching RentCast data: {e}")
        return {
            'success': False,
            'error': 'An unexpected error occurred. Please try again.'
        }
