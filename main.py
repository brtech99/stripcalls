# stripcall for natloff
import os
import logging
import re

from google.cloud import secretmanager
from twilio.rest import Client
from flask import Flask, request
from google.cloud import datastore
from flask import jsonify # Import jsonify

from twilio.twiml.messaging_response import MessagingResponse

app = Flask(__name__)
# Configure basic logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


def access_secret_version(project_id, secret_name): # Added a space before comment for consistency
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


# Define the range of simulator numbers
SIMULATOR_NUMBER_PREFIX = '+1202555100'
SIMULATOR_NUMBER_RANGE_START = 0
SIMULATOR_NUMBER_RANGE_END = 9

def is_simulator_number(phone_number):
    """Checks if a phone number is within the simulator range."""
    if phone_number.startswith(SIMULATOR_NUMBER_PREFIX):
        try:
            last_digit = int(phone_number[-1])
            return SIMULATOR_NUMBER_RANGE_START <= last_digit <= SIMULATOR_NUMBER_RANGE_END
        except ValueError:
            return False
    return False


def send_message_to_group(sender_identity, sender_group, original_message, to_number) -> list[tuple[str, str]]:
    """Sends a message to all members of a group, excluding the sender."""
    simulator_messages = [] # List to hold messages for simulators

    logger.debug(f"Attempting to send message to group: {sender_group} from sender: {sender_identity}")

    # Query Datastore for all entities in the sender's group
    query = datastore_client.query(kind='numbr')
    query.add_filter(sender_group, '=', True) # Assuming a boolean property for each group (medic, armorer, natloff)

    try:
        group_members = list(query.fetch())
        logger.debug(f"Found {len(group_members)} members in group {sender_group}")

        for member in group_members:
            member_name = member.get('name')
            member_phone = member.get('phonNbr')

            # Construct the outgoing message
            outgoing_message = f"{sender_identity}: {original_message}"

            # Avoid sending the message back to the sender
            if member_name == sender_identity or member_phone == sender_identity:
                logger.debug(f"Skipping sending message to sender: {sender_identity}")
                continue

            # Determine if sending to simulator or Twilio
            if is_simulator_number(member_phone):
                logger.debug(f"Sending message to simulator number: {member_phone} with message: {outgoing_message}")
                simulator_messages.append((member_phone, outgoing_message, sender_identity))
            else:
                logger.debug(f"Sending message to Twilio number: {member_phone} with message: {outgoing_message}")
                # Logic to send message via Twilio API
                try:
                    message = twilio_client.messages.create(
                        to=member_phone, # Recipient's phone number
                        body=outgoing_message
                    )
                    logger.debug(f"Message sent to {member_phone}. SID: {message.sid}")
                except Exception as twilio_e:
                    logger.error(f"Error sending message via Twilio to {member_phone}: {twilio_e}")



    except Exception as e:
        logger.error(f"Error sending message to group {sender_group}: {e}", exc_info=True)
        # Handle the error, perhaps log it and inform the sender that sending failed.

    return simulator_messages

try:
    datastore_client = datastore.Client(project=os.environ.get('DATASTORE_PROJECT_ID')) # Added a space before comment
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

    response_message = "" # Initialize an empty string to store the response message

    # Enhanced sender identification: prioritize searching by phonNbr, then by name
    sender_entity = None

    if datastore_client:
        # Attempt to find sender by phonNbr (original number)
        query = datastore_client.query(kind='numbr')
        query.add_filter('phonNbr', '=', from_number)
        results = list(query.fetch())
        if results:
            logger.info(f"Sender found by phonNbr (original): {from_number}")
            sender_entity = results[0]

        # If not found by phonNbr (original) and number starts with +1, try phonNbr (modified)
        if not sender_entity and from_number.startswith("+1"):
            modified_from_number = from_number[2:]
            query = datastore_client.query(kind='numbr')
            query.add_filter('phonNbr', '=', modified_from_number)
            results = list(query.fetch())
            if results:
                logger.info(f"Sender found by phonNbr (modified): {modified_from_number}")
                sender_entity = results[0]

        # If still not found, attempt to find sender by name (original number) - less likely for users
        if not sender_entity:
            query = datastore_client.query(kind='numbr')
            query.add_filter('name', '=', from_number)
            results = list(query.fetch())
            if results:
                logger.info(f"Sender found by name (original): {from_number}")
                sender_entity = results[0]

    if sender_entity:
        sender_identity = sender_entity.get('name')
        # Determine sender_group based on boolean properties
        if sender_entity.get('medic', False):
            sender_group = 'medic'
        elif sender_entity.get('armorer', False):
            sender_group = 'armorer'
        elif sender_entity.get('natloff', False):
            sender_group = 'natloff'
        else:
            # Default group if none of the specific group flags are True
            sender_group = 'ref' # Or another appropriate default
        logger.info(f"Sender found: {sender_identity}, Group: {sender_group}")
    else:
        logger.info("Sender not found in Datastore. Treating as guest.")
        sender_identity = from_number
        sender_group = "ref"

    # Define parse_phone_number function inside webhook or import it if defined elsewhere
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

        # Rule 5: "(XXX) YYY-ZZZZ" format
        if len(phone_str) == 14 and re.match(r"^\(\d{3}\)\s\d{3}-\d{4}$", phone_str):
             cleaned_number = re.sub(r"[()\s-]", "", phone_str)
             if len(cleaned_number) == 10 and cleaned_number.isdigit():
                 logger.info(f"Parsed as US number: {cleaned_number}")
                 return cleaned_number, "US"
        return None, None

    messages_to_send = [] # Initialize an empty list to store messages to be sent

    if body.startswith("+"):
        # Handle command
        command_parts = body[1:].split()  # Remove "+" and split by spaces
        command = command_parts[0].lower() if command_parts else None
        parameters = command_parts[1:] if len(command_parts) > 1 else []

        # Initialize a list to hold potential messages from command processing
        command_messages = []

        if command == "help":
            command_messages.append({'to': from_number, 'body': "Available commands: /help, /status, +medic [name] [phone], +armorer [name] [phone], +natloff [name] [phone], +remove [name], +list [group]"})
        elif command == "status":
            command_messages.append({'to': from_number, 'body': "Service is operational."})
        elif command in ["medic", "armorer", "natloff"]:
            # Command to add or update a user
            if len(parameters) < 2:
                command_messages.append({'to': from_number, 'body': f"Invalid syntax for +{command}. Usage: +{command} [name] [phone]"})
            else:
                name = parameters[0]
                phone_str = parameters[1]

                # Attempt to parse the phone number, handling various formats
                phone_number, phone_format = parse_phone_number(phone_str)
                logger.debug(f"Parsed phone number from command: {phone_number}, format: {phone_format}")

                if not phone_number:
                    logger.debug(f"Phone number parsing failed for command: {phone_str}")
                    command_messages.append({'to': from_number, 'body': f"Could not parse phone number: {phone_str}. Please use a valid format (e.g., 1234567890, +11234567890, (123) 456-7890, 123-456-7890)."})
                else:
                    # Standardize phone number to E.164 format if it's a US number
                    if phone_format == "US":
                        phone_number = "+1" + phone_number

                    # 1. Look up by name
                    logger.debug(f"Querying Datastore for name: {name}")
                    query_by_name = datastore_client.query(kind='numbr')
                    query_by_name.add_filter('name', '=', name)
                    results_by_name = list(query_by_name.fetch())
                    entity_by_name = results_by_name[0] if results_by_name else None

                    if entity_by_name:
                        # Name exists
                        stored_phone_number = entity_by_name.get('phonNbr')
                        logger.debug(f"Result of name query: {entity_by_name}")

                        if stored_phone_number == phone_number:
                            # Name and number match an existing entry, update groups
                            entity_by_name['armorer'] = command == 'armorer'
                            entity_by_name['medic'] = command == 'medic'
                            entity_by_name['natloff'] = command == 'natloff'
                            datastore_client.put(entity_by_name)
                            command_messages.append({'to': from_number, 'body': f"Updated groups for {name} with number {phone_str}."})
                            # Optional: Notify the user if their groups were updated
                            # command_messages.append({'to': phone_number, 'body': f"Your groups have been updated."})

                        else:
                            # Name exists, number differs. Check if the provided number exists elsewhere.
                            query_by_number = datastore_client.query(kind='numbr')
                            logger.debug(f"Querying Datastore for phone number (name exists): {phone_number}")
                            query_by_number.add_filter('phonNbr', '=', phone_number)
                            results_by_number = list(query_by_number.fetch())
                            logger.debug(f"Result of number query (name exists): {entity_by_number_lookup}")
                            entity_by_number_lookup = results_by_number[0] if results_by_number else None

                            if entity_by_number_lookup:
                                # Provided number exists with another name
                                other_name = entity_by_number_lookup.get('name')
                                command_messages.append({'to': from_number, 'body': f"Error: That telephone number is associated with {other_name}."})
                            else:
                                # Name exists, numbers differ, new number not found elsewhere. Update the existing record's phone number and groups.
                                entity_by_name['armorer'] = command == 'armorer'
                                entity_by_name['phonNbr'] = phone_number
                                entity_by_name['medic'] = command == 'medic'
                                entity_by_name['natloff'] = command == 'natloff'
                                logger.debug(f"Updating phone number for existing entry {name} to {phone_number}")
                                datastore_client.put(entity_by_name)
                                command_messages.append({'to': from_number, 'body': f"Updated phone number for {name} to {phone_str}."})
                                response_message = f"Updated phone number for {name} to {phone_str}."
                    else:
                        # No entity found with the same name. Check if the provided number exists elsewhere.
                        query_by_number = datastore_client.query(kind='numbr')
                        logger.debug(f"Querying Datastore for phone number (name not found): {phone_number}")
                        query_by_number.add_filter('phonNbr', '=', phone_number)
                        results_by_number = list(query_by_number.fetch())
                        entity_by_number_lookup = results_by_number[0] if results_by_number else None
                        logger.debug(f"Result of number query (name not found): {entity_by_number_lookup}")
                        if entity_by_number_lookup:
                            # Provided number exists with another name
                            other_name = entity_by_number_lookup.get('name')
                            command_messages.append({'to': from_number, 'body': f"Error: That telephone number is associated with {other_name}."})
                        else:
                            # Neither name nor number exists. Create a new record.
                            logger.debug("Creating new entity")
                            key = datastore_client.key('numbr')
                            new_entity = datastore.Entity(key)
                            new_entity.update({
                                'phonNbr': phone_number,
                                'name': name,
                                'admin': False,
                                'armorer': command == 'armorer',
                                'ref': False, # Assuming 'ref' is not set by these commands
                                'super': False, # Assuming 'super' is not set by these commands
                                'active': True, # Assuming new entries are active
                                'ucName': name.upper(), # Assuming uppercase name for ucName
                                'medic': command == 'medic',
                                'natloff': command == 'natloff'
                            })
                            datastore_client.put(new_entity)
                            logger.debug(f"New entity created for {name} with number {phone_number}")
                            command_messages.append({'to': from_number, 'body': f"Created entry for {name} with number {phone_str}."})
                            command_messages.append({'to': phone_number, 'body': f"You have been added to the USA Fencing {command} list as {name}."})

        elif command == "remove":
            if len(parameters) != 1:
                command_messages.append({'to': from_number, 'body': "Invalid syntax for +remove. Usage: +remove [name]"})
            else:
                name = parameters[0]
                query_by_name = datastore_client.query(kind='numbr')
                query_by_name.add_filter('name', '=', name)
                results_by_name = list(query_by_name.fetch())
                entity_to_delete = results_by_name[0] if results_by_name else None # Use entity_to_delete

                if entity_to_delete:
                    datastore_client.delete(entity_to_delete.key)
                    response_message = f"Entry for {name} deleted."
                else:
                    response_message = f"Entry for {name} not found."

        elif command == "list":
            if len(parameters) > 1: # Allow empty parameter for default list
                 command_messages.append({'to': from_number, 'body': "Invalid syntax for +list. Usage: +list [group] (where group is medic, armorer, or natloff)"})
            else:
                group_filter = "armorer"  # Default to armorer if no parameter is provided
                if len(parameters) == 1:
                    param = parameters[0].lower()
                    if param in ["medic", "armorer", "natloff"]:
                        group_filter = param
                    else:
                        response_message = "invalid syntax"

                if not command_messages: # Only proceed if syntax was valid and no error message was added
                    query = datastore_client.query(kind='numbr')
                    query.add_filter(group_filter, '=', True)
                    results = list(query.fetch())

                    if results:
                        # Build the response message
                        entries = []
                        for entity in results:
                            name = entity.get('name', 'Unknown')
                            phone = entity.get('phonNbr', 'N/A')
                            entries.append(f"{name} {phone}")
                        list_response = f"List for {group_filter}:\n" + ", ".join(entries)
                        command_messages.append({'to': from_number, 'body': list_response})
                    else:
                        command_messages.append({'to': from_number, 'body': f"No entries found for {group_filter}."})

        # Add command messages to the main list of messages to send
        messages_to_send.extend(command_messages)

    else:
        # Non-command message - send to group
        if sender_group:
            logger.info(f"Identified sender group: {sender_group}")
            # Call the function to send the message to the group and collect simulator messages
            group_messages_for_simulators = send_message_to_group(sender_identity, sender_group, body, to_number)
            # Add the group messages intended for simulators to the main list
    # Process all messages in the messages_to_send list
    simulator_twiML_messages = []

    if not messages_to_send: # If no messages to send, return empty response
        resp = MessagingResponse() # Create MessagingResponse only for commands
        logger.debug(response_message) # Log response_message only for commands
    else:
        # The logic to send to a group based on the sender's number and the else block for commands
        # sender_identity and sender_group are determined by the Datastore lookup above
        if sender_group:
            logger.info(f"Identified sender group: {sender_group}")
            # The logic to send to a group based on the sender's number and the else block for commands

    # Iterate through messages_to_send and send them
    for message in messages_to_send:
        if is_simulator_number(message['to']):
            simulator_twiML_messages.append(message)
        else:
            # Send message via Twilio to non-simulator numbers
            try:
                twilio_client.messages.create(
                    to=message['to'],
                    body=message['body'],
                    from_=to_number # Use the receiving Twilio number as the sender
                )
                logger.debug(f"Sent message to Twilio number {message['to']}: {message['body']}")
            except Exception as e:
                logger.error(f"Error sending message via Twilio to {message['to']}: {e}")

    # Generate TwiML response for simulator messages
    resp = MessagingResponse()
    if simulator_twiML_messages:
        for msg in simulator_twiML_messages:
            resp.message(msg['body'], to=msg['to'])

    return str(resp)
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

@app.route('/get_name', methods=['GET'])
def get_name():
    """
    Looks up a name in Datastore by phone number and returns it as JSON.
    """
    phone_number = request.args.get('phoneNumber')
    logger.info(f"Received request for name lookup for phone number: {phone_number}")

    name = None

    if datastore_client and phone_number:
        # Attempt to find sender by phonNbr (original number)
        query = datastore_client.query(kind='numbr')
        query.add_filter('phonNbr', '=', phone_number)
        results = list(query.fetch())
        if results and results[0].get('name'):
            name = results[0].get('name')
            logger.info(f"Name found for {phone_number}: {name}")
        elif not results and phone_number.startswith("+1"):
            # If not found by phonNbr (original) and number starts with +1, try phonNbr (modified)
            modified_phone_number = phone_number[2:]
            query = datastore_client.query(kind='numbr')
            query.add_filter('phonNbr', '=', modified_phone_number)
            results = list(query.fetch())
            if results and results[0].get('name'):
                name = results[0].get('name')
                logger.info(f"Name found for {phone_number} (modified): {name}")

    return jsonify({'name': name}) # Return JSON response with name or None

