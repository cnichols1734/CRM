import os
import json
import sendgrid
from sendgrid.helpers.mail import *
import urllib3
import certifi

# Disable SSL warnings for development
urllib3.disable_warnings()

def test_sendgrid_templates():
    # Get API key from environment or input
    api_key = os.getenv('SENDGRID_API_KEY') or input('Enter your SendGrid API key: ')
    print(f"\nUsing API key ending in: ...{api_key[-4:]}")
    
    # Specific template ID to test
    template_id = "d-0043b7d4d4ae440daf47bcb2ff64d3c0"

    try:
        # Initialize SendGrid client
        sg = sendgrid.SendGridAPIClient(api_key=api_key)
        
        # Set SSL verification
        if os.path.exists('/etc/ssl/cert.pem'):
            sg.client.useragent.session.verify = '/etc/ssl/cert.pem'
        else:
            sg.client.useragent.session.verify = certifi.where()

        print("\n1. Testing API Key validity...")
        try:
            # First, test if the API key is valid by making a simple request
            response = sg.client.api_keys.get()
            print(f"API Key test - Status Code: {response.status_code}")
            print(f"API Key test - Response: {response.body.decode('utf-8')}\n")
        except Exception as e:
            print(f"Error testing API key: {str(e)}\n")

        print("2. Attempting to fetch all templates...")
        try:
            # Get all templates
            response = sg.client.templates.get()
            
            print(f"Templates request - Status Code: {response.status_code}")
            print("\nRaw Response Headers:")
            print(json.dumps(dict(response.headers), indent=2))
            
            print("\nRaw Response Body:")
            body = response.body.decode('utf-8')
            print(json.dumps(json.loads(body), indent=2))
            
            # Parse templates
            templates_data = json.loads(body)
            if 'templates' in templates_data:
                templates = templates_data['templates']
                print(f"\nFound {len(templates)} templates")
                
                for template in templates:
                    print(f"\nTemplate Details:")
                    print(f"  Name: {template.get('name')}")
                    print(f"  ID: {template.get('id')}")
                    print(f"  Version Count: {len(template.get('versions', []))}")
            else:
                print("\nNo 'templates' key found in response")
                
        except Exception as e:
            print(f"Error fetching templates: {str(e)}")

        print(f"\n3. Attempting to fetch specific template (ID: {template_id})...")
        try:
            # Get specific template
            response = sg.client.templates._(template_id).get()
            
            print(f"Single template request - Status Code: {response.status_code}")
            print("\nRaw Response Headers:")
            print(json.dumps(dict(response.headers), indent=2))
            
            print("\nRaw Response Body:")
            body = response.body.decode('utf-8')
            template_data = json.loads(body)
            print(json.dumps(template_data, indent=2))
            
            print(f"\nTemplate Details:")
            print(f"  Name: {template_data.get('name')}")
            print(f"  ID: {template_data.get('id')}")
            print(f"  Version Count: {len(template_data.get('versions', []))}")
            
        except Exception as e:
            print(f"Error fetching specific template: {str(e)}")
            
    except Exception as e:
        print(f"General error: {str(e)}")

if __name__ == "__main__":
    print("SendGrid Template API Test Script")
    print("================================")
    test_sendgrid_templates() 