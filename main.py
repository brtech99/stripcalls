import os
import logging
import re

from google.cloud import secretmanager
from twilio.rest import Client
from flask import Flask, request
from google.cloud import datastore

app = Flask(__name__)
from twilio.twiml.messaging_response import MessagingResponse

# Configure basic logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


def access_secret_version(project_id, secret_name):
    """
    Access the payload for the given secret version if one exists.
    """
    try:
        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{project_id}/secrets/{secret_name}/versions/latest"
        response = client.access_secret_version(name=name)
        return response.payload.data.decode("UTF-8")
    except Exception as e:
        logger.error(f"Error accessing secret {secret_name}: {e}")
        return None


try:
    datastore_client = datastore.Client(project=os.environ.get('DATASTORE_PROJECT_ID'))
    logger.debug("Datastore client initialized successfully.")
except Exception as e:
    logger.error(f"Error connecting to Datastore: {e}")
    datastore_client = None
    logger.error("Datastore client initialization failed.")


project_id = os.environ.get('GOOGLE_CLOUD_PROJECT')
twilio_account_sid_secret_name = 'twilio_account_sid'
twilio_auth_token_secret_name = 'twilio_auth_token'

twilio_account_sid = access_secret_version(project_id, twilio_account_sid_secret_name)
twilio_auth_token = access_secret_version(project_id, twilio_auth_token_secret_name)

if twilio_account_sid and twilio_auth_token:
    twilio_client = Client(twilio_account_sid, twilio_auth_token)
else:
    logger.error("Could not retrieve Twilio credentials from Secret Manager")


@app.route('/webhook', methods=['POST'])
def webhook():
    """
    Handle incoming Twilio webhook requests.
    """
    from_number = request.form.get('From')
    to_number = request.form.get('To')
    body = request.form.get('Body')

    logger.info(
        f"Incoming message - From: {from_number}, To: {to_number}, Body: {body}"
    )
    # first query
    query = datastore_client.query(kind='numbr')
    query.add_filter('name', '=', from_number)
    results = list(query.fetch())
    sender_entity = None
    if results:
        logger.info("Sender found in Datastore using original number.")
        sender_entity = results[0]
    elif from_number.startswith("+1"):
        # second query
        modified_from_number = from_number[2:]
        query = datastore_client.query(kind='numbr')
        query.add_filter('name', '=', modified_from_number)
        results = list(query.fetch())
        if results:
            logger.info("Sender found in Datastore using modified number.")
            sender_entity = results[0]
    if sender_entity:
        sender_identity = sender_entity.get('name')
        sender_group = sender_entity.get('group')
    else:
        logger.info("Sender not found in Datastore. Treating as guest.")
        sender_identity = from_number
        sender_group = "ref"

    def parse_phone_number(phone_str):
        """Parses and validates a phone number from a string, returning a cleaned number or None."""
        # Remove common separators
        cleaned_number = re.sub(r"[-()\s]", "", phone_str)

        # Rule 1: Starts with "+" and digits
        if cleaned_number.startswith("+") and cleaned_number[1:].isdigit():
            logger.info(f"Parsed as international number: {cleaned_number}")
            return cleaned_number, "international"

        # Rule 2: Exactly 10 digits
        if len(cleaned_number) == 10 and cleaned_number.isdigit():
            logger.info(f"Parsed as US number: {cleaned_number}")
            return cleaned_number, "US"

        # Rule 4: "XXX-YYY-ZZZZ" format
        if len(phone_str) == 12 and re.match(r"^\d{3}-\d{3}-\d{4}$", phone_str):
             cleaned_number = re.sub(r"[-]", "", phone_str)
             logger.info(f"Parsed as US number: {cleaned_number}")
             return cleaned_number, "US"
        return None, None

    if body.startswith("+"):
        # Handle command
        command_parts = body[1:].split()  # Remove "+" and split by spaces
        command = command_parts[0].lower() if command_parts else None
        parameters = command_parts[1:] if len(command_parts) > 1 else []

        if command in ["medic", "armorer", "natloff"]:
            if len(parameters) < 2:
                return f"Error: The +{command} command requires a name and a phone number."

            name = parameters[0]
            phone_str = parameters[1]
            phone_number, phone_format = parse_phone_number(phone_str)

            if not phone_number:
                return f"Error: Invalid phone number format for {phone_str}."

            # 1. Look up by name
            query_by_name = datastore_client.query(kind='numbr')
            query_by_name.add_filter('name', '=', name)
            results_by_name = list(query_by_name.fetch())

            if results_by_name:
                # Found an entity with the same name
                entity = results_by_name[0]
                stored_phone_number = entity.get('number')

                if stored_phone_number == phone_number:
                    return f"Entry for {name} with number {phone_str} already exists."
                else:
                    # Name exists, but numbers differ. Check if the new number exists elsewhere.
                    query_by_number = datastore_client.query(kind='numbr')
                    query_by_number.add_filter('number', '=', phone_number)
                    results_by_number = list(query_by_number.fetch())

                    if results_by_number:
                        # The number is associated with another entry
                        other_entity = results_by_number[0]
                        other_name = other_entity.get('name')
                        return f"That telephone number ({phone_str}) is already associated with {other_name}."
                    else:
                        # Name exists, numbers differ, new number not found elsewhere. Update the existing entity.
                        entity['number'] = phone_number
                        datastore_client.put(entity)
                        return f"Updated telephone number for {name} to {phone_str}."
            else:
                # No entity found with the same name. Check if the phone number exists elsewhere.
                query_by_number = datastore_client.query(kind='numbr')
                query_by_number.add_filter('number', '=', phone_number)
                results_by_number = list(query_by_number.fetch())

                if results_by_number:
                    # The number is associated with another entry
                    other_entity = results_by_number[0]
                    other_name = other_entity.get('name')
                    return f"That telephone number ({phone_str}) is already associated with {other_name}."
                else:
                    # Neither name nor number exists. Create a new entity.
                    new_entity = datastore_client.entity(datastore_client.key('numbr'))
                    new_entity.update({
                        'name': name,
                        'number': phone_number,
                        'admin': False, # Assuming new entries are not admins by default
                        'group': command # Use the command (medic, armorer, natloff) as the group
                    })
                    datastore_client.put(new_entity)
                    return f"Created new entry for {name} with telephone number {phone_str} in group {command}."
        else:
            # Handle other commands or non-command messages
            response = MessagingResponse()
            response.message(f"Received unknown command: {command}")
            return str(response)


    if twilio_client:        
        # Check if the 'to' number is within the simulator range (+12025551000 to +12025551099)
        if not (from_number.startswith("+120255510") and 0 <= int(from_number[10:]) <= 9):
            # If not within the simulator range, send via Twilio
            try:
                twilio_client.messages.create(
                    to=from_number, from_=to_number, body=f"Received: {body}" # Or whatever response you want
 ) # This part might need adjustment based on how you want to respond to commands
            except Exception as e:
                logger.error(f"Error sending Twilio message: {e}")

    return ""


@app.route('/')
def hello_world():
    logger.debug(f"Value of datastore_client at start of hello_world: {datastore_client}")
    print(f"Value of datastore_client at start of hello_world: {datastore_client}")
    response_string = ""
    if datastore_client is None:
        response_string = response_string + "Could not connect to Datastore. "
        logger.error("Could not connect to Datastore")        
    try:
        query = datastore_client.query(kind='numbr')
        results = list(query.fetch())
        entity = None
        for result in results:
             entity = result
             print(f"Entity Name: {entity.get('name')}")
             if entity.get("name") == "Brian":
                print("Brian was found")
                logger.info("Brian was found")
                
             break        
    except Exception as e:
        logger.error(f"Error querying Datastore: {e}")
        return f"Error: Could not query Datastore. {e}", 500

    if entity:
        response_string = response_string + f"Hello, world! Project ID: {project_id}. Found entity with name: {entity.get('name')}, admin: {entity.get('admin')}"
        return response_string
    else:
        response_string = response_string + f"Hello, world! Project ID: {project_id}. User 'Brian' not found."
        return response_string   
#test  