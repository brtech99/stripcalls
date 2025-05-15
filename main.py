# stripcall for natloff
import os
import logging
import re

from dotenv import load_dotenv
from google.cloud import secretmanager
from twilio.rest import Client
from flask import Flask, request
from google.cloud import datastore 
from flask import jsonify # Import jsonify

from twilio.twiml.messaging_response import MessagingResponse # Added a space before comment for consistency

app = Flask(__name__)
# Configure basic logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)
if not (app in locals()): 
    logger.debug("No app 1")


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

# Load environment variables from .env file
load_dotenv()

# Read Twilio numbers from environment variables
ARMORER_TWILIO_NUMBER = os.getenv('ARMORER_TWILIO_NUMBER')
MEDIC_TWILIO_NUMBER = os.getenv('MEDIC_TWILIO_NUMBER')
NATLOFF_TWILIO_NUMBER = os.getenv('NATLOFF_TWILIO_NUMBER')

# Define the range of simulator numbers
SIMULATOR_NUMBER_PREFIX = '+1202555100'
SIMULATOR_NUMBER_RANGE_START = 0
SIMULATOR_NUMBER_RANGE_END = 9


def is_simulator_number(phone_number):
    """Checks if a phone number is a valid simulator number (length 12, correct prefix, last char is a digit)."""
    # Remove '+1' prefix if it exists
    cleaned_number = phone_number.lstrip('+1')

    if len(cleaned_number) == 10 and cleaned_number.startswith('202555100'):
        try:
            int(cleaned_number[-1]) # Check if the last character is a digit (0-9)
            return 0 <= int(cleaned_number[-1]) <= 9
        except ValueError:
            pass # Not a digit
    return False # Doesn't match the pattern


def find_entity_by_name(datastore_client, name):
    """Finds an entity by name in Datastore."""
    query = datastore_client.query(kind='numbr')
    query.add_filter('name', '=', name)
    results = list(query.fetch())
    entity = results[0] if results else None
    logger.debug(f"find_entity_by_name name={name}, entity={entity}")
    return entity, entity is not None

def find_entity_by_number(datastore_client, phone_number):
    """Finds an entity by phone number in Datastore."""
    # Datastore always has phone numbers without +1.
    cleaned_number = phone_number.lstrip('+1')
    query = datastore_client.query(kind='numbr')
    query.add_filter('phonNbr', '=', cleaned_number)
    results = list(query.fetch())
    entity = results[0] if results else None
    logger.debug(f"find_entity_by_number original={phone_number}, cleaned={cleaned_number}, results={entity}")
    return entity, entity is not None
    
def send_single_message(to_number, body, from_number, all_simulator_messages, twilio_client):
    """Sends a single message to either a simulator or via Twilio."""
    if is_simulator_number(to_number):
        # Format the to_number for simulator.html (remove +1 if exists, ensure it starts with 1 if it's a simulator number)
        formatted_to_number = to_number.lstrip('+') # Remove leading + if present
        if formatted_to_number.startswith('1') and len(formatted_to_number) > 10:
             formatted_to_number = formatted_to_number[1:] # Remove the leading '1' if it's already there and part of a longer number
        formatted_to_number = '1' + formatted_to_number # Prepend '1' for consistency with HTML IDs
        formatted_from_number = from_number
        if is_simulator_number(from_number) and not from_number.startswith('+1'):
            formatted_from_number = '+1' + from_number
        message_data = {'to': to_number, 'body': body, 'from_': formatted_from_number}
        all_simulator_messages.append(message_data)
        logger.debug(f"send_single_message to simulator {to_number} from {formatted_from_number} body {body}")
    else:
        try:
            twilio_client.messages.create(to=to_number, body=body, from_=from_number)
            logger.debug(f"Sent message to Twilio number {to_number}: {body}")
        except Exception as e:
            logger.error(f"Error sending message via Twilio to {to_number}: {e}")


def send_message_to_group(sender_identity, sender_group, original_message, from_number, all_simulator_messages, twilio_client):

    logger.debug(f"Attempting to send message to group: {sender_group} from sender: {sender_identity}")
    logger.debug(f"Original message to send: {original_message}")

    # Query Datastore for all entities in the sender's group
    query = datastore_client.query(kind='numbr')
    query.add_filter(sender_group, '=', True) # Assuming a boolean property for each group (medic, armorer, natloff)

    try:

        group_members = list(query.fetch())
        logger.debug(f"Found {len(group_members)} members in group {sender_group}")
        logger.debug(f"Group members are: {group_members}")
        for member in group_members:
            member_name = member.get('name')
            member_phone = member.get('phonNbr')
            menber_active = member.get('active')
            # Prepend +1 if the phone number is a 10-digit number
            if member_phone and len(member_phone) == 10 and member_phone.isdigit():
                member_phone = '+1' + member_phone
            logger.debug(f"Processing member: Name: {member_name}, Phone: {member_phone}")
            # Construct the outgoing message
            outgoing_message = f"{sender_identity}: {original_message}" 
            if (member_name == sender_identity) or not(member_active):
                logger.debug(f"Skipping sending message to sender: {sender_identity}")
            else:
                send_single_message(member_phone, outgoing_message, from_number, all_simulator_messages, twilio_client)
                logger.debug(f"Send Message to {member_phone} from {from_number} body {outgoing_message}")
    except Exception as e:
        logger.error(f"Error in send_message_to_group: {e}", exc_info=True)


# Initialize Google Cloud Datastore client
datastore_client = None
try:
    datastore_client = datastore.Client(project=os.environ.get('DATASTORE_PROJECT_ID'))
    logger.info("Datastore client initialized successfully.")
except Exception as e:
    logger.error(f"Failed to initialize Datastore client: {e}")
project_id = os.environ.get('GOOGLE_CLOUD_PROJECT')
twilio_account_sid_secret_name = 'twilio_account_sid'
twilio_auth_token_secret_name = 'twilio_auth_token'

twilio_account_sid = access_secret_version(project_id, twilio_account_sid_secret_name)
twilio_auth_token = access_secret_version(project_id, twilio_auth_token_secret_name)

if twilio_account_sid and twilio_auth_token:
    twilio_client = Client(twilio_account_sid, twilio_auth_token)
    logger.debug("successfully created twilio client")
else:
    logger.error("Could not retrieve Twilio credentials from Secret Manager")


@app.route('/webhook', methods=['POST'])
def webhook():
    """
    Handle incoming Twilio webhook requests.
    """
    logger.debug(f"Incoming webhook request form data: {request.form}")
    # Determine the recipient group based on the 'To' number
    to_number = request.form.get('To')
    from_number = request.form.get('From')
    body = request.form.get('Body')
    from_group = None
    if to_number == ARMORER_TWILIO_NUMBER:
        from_group = 'armorer'
        idx = 1
    elif to_number == MEDIC_TWILIO_NUMBER:
        from_group = 'medic'
        idx = 2
    elif to_number == NATLOFF_TWILIO_NUMBER:
        from_group = 'natloff'
        idx = 3


    # Fetch glbvar entity from Datastore for non-command logic (needs to be available later)
    glbvar_entity = None
    cbp = 1 # Initialize cbp as integer, default to 1 (for 1-4 cycle)
    cb = [""] * 5 # Initialize cb as a list of 5 empty strings to match expected indices 1-4

    if datastore_client and idx is not None:
            query = datastore_client.query(kind='glbvar')
            query.add_filter('idx', '=', idx)
            results = list(query.fetch())
            if results:
                glbvar_entity = results[0]
                # Get cbp as integer, default to 1 for the 1-4 cycle
                cbp = glbvar_entity.get('cbp', 1) 
                cb = glbvar_entity.get('cb', [""] * 5) # Get cb as list, default to list of 5 empty strings
                logger.debug(f"Fetched glbvar entity for idx {idx}. cbp: {cbp}, cb: {cb}")
            else:
                logger.warning(f"No glbvar entity found for idx {idx}")
    else:
        logger.error("datastore client not set or idx not set")

    logger.info(
        f"Incoming message - From: {from_number}, from_group: {from_group}, To: {to_number}, Body: {body}"
    )


    # Enhanced sender identification: prioritize searching by phonNbr, then by name
    sender_entity = None
    sender_identity = from_number
    sender_is_in_group = False # Initialize sender_is_in_group as boolean False
    sender_is_ref = False # Initialize sender_is_ref as boolean False
    is_sender_simulator = is_simulator_number(from_number)

    if datastore_client:
        # Attempt to find sender by phonNbr (original number)
        sender_entity, sender_found = find_entity_by_number(datastore_client, from_number)

        if sender_found:
            # Add +1 prefix if the phone number is a 10-digit number from Datastore
            if sender_entity.get('phonNbr') and len(sender_entity['phonNbr']) == 10 and sender_entity['phonNbr'].isdigit():
                sender_entity['phonNbr'] = '+1' + sender_entity['phonNbr']
            sender_identity = sender_entity.get('name')
            logger.debug(f"from_group={from_group} sender_group={sender_entity.get(from_group)}")
            sender_is_in_group = sender_entity.get(from_group, False) # Get group status safely
            sender_is_ref = sender_entity.get('ref', False)
            logger.debug(f"Sender identity {sender_identity} is in group={sender_is_in_group} ref={sender_is_ref}") 
        # if sender_entity is not defined, then sender is a guest, and identity is from_number
        else:
            logger.debug(f"Sender number {from_number} not found")

    # Define parse_phone_number function inside webhook or import it if defined elsewhere
    def parse_phone_number(phone_str):
        """Parses and validates a phone number from a string, returning a cleaned E.164 number or None."""
        # Remove common separators (hyphens, parentheses, whitespace)
        cleaned_number = re.sub(r"[-()\s]", "", phone_str)

        if not cleaned_number:
            return None

        # Check for a leading '+'
        if cleaned_number.startswith("+"):
            # Assume international or already E.164, return as is after removing other non-digits
            # Further validation for E.164 US number (+1 followed by 10 digits)
            if cleaned_number.startswith("+1") and len(cleaned_number) == 12 and cleaned_number[1:].isdigit():
                logger.info(f"Parsed as E.164 US number: {cleaned_number}")
                return cleaned_number
            elif cleaned_number[1:].isdigit(): # It's a valid international number (starts with + and followed by digits)
                 logger.info(f"Parsed as international number: {cleaned_number}")
                 return cleaned_number
            else: # Starts with + but not a valid E.164 or international format
                 return None
        else:
        # Rule 2: Exactly 10 digits
            if len(cleaned_number) == 10 and cleaned_number.isdigit():
                 logger.info(f"Parsed as US number: +1{cleaned_number}")
                 return "+1" + cleaned_number
            # Add other formats if necessary

            return None # Didn't match any recognized format

    all_simulator_messages = [] # Initialize a list to collect all simulator messages
    # The rest of the webhook function follows...
    if body.startswith("+"):
        command_parts = body[1:].split(maxsplit=1)  # Remove "+" and split into command and rest
        command = command_parts[0].lower() if command_parts else None
        parameters = command_parts[1].split() if len(command_parts) > 1 else [] # Split rest into parameters

        # Initialize a list to hold messages generated by command processing
        command_messages = []

        # Authorization check for commands
        is_authorized_command_user = sender_entity and (sender_entity.get('admin', False) or sender_entity.get('super', False))

 # This block handles all commands, with individual authorization checks
        if command == "help":
            command_messages.append({'to': from_number, 'body': "Available commands: +help, +armorer, +medic , +natloff , +remove, +list [group], +activate, +deactivate, +admin, +deadmin, user"})
        elif command == "activate":
            if len(parameters) == 0:
            # Activate the sender
                if sender_entity:
                    sender_entity['active'] = True
                    datastore_client.put(sender_entity)
                    command_messages.append({'to': from_number, 'body': "Your account has been activated."})
                else:
                    command_messages.append({'to': from_number, 'body': "Could not activate your account. Please register first."})
            elif len(parameters) == 1:
            # Activate a named user (requires admin/super)
                if not is_authorized_command_user:
                    command_messages.append({'to': from_number, 'body': "You are not authorized to activate other users."})
                else:
                    name_to_activate = parameters[0]
                    entity_to_activate, found = find_entity_by_name(datastore_client, name_to_activate)

                    if entity_to_activate:
                        entity_to_activate['active'] = True
                        datastore_client.put(entity_to_activate)
                        command_messages.append({'to': from_number, 'body': f"Account for {name_to_activate} has been activated."})
                    # Optionally notify the activated user: send_single_message(entity_to_activate.get('phonNbr'), "Your account has been activated by an admin.", to_number, all_simulator_messages, twilio_client)
                    else:
                        command_messages.append({'to': from_number, 'body': f"User '{name_to_activate}' not found."})
            else:
                command_messages.append({'to': from_number, 'body': "Invalid syntax for +activate. Usage: +activate [name]"})

        elif command == "deactivate":
            if len(parameters) == 0:
            # Deactivate the sender
                if sender_entity:
                    sender_entity['active'] = False
                    datastore_client.put(sender_entity)
                    command_messages.append({'to': from_number, 'body': "Your account has been deactivated."})
                else:
                    command_messages.append({'to': from_number, 'body': "Could not deactivate your account. Please register first."})
            elif len(parameters) == 1:
            # Deactivate a named user (requires admin/super)
                if not is_authorized_command_user:
                    command_messages.append({'to': from_number, 'body': "You are not authorized to deactivate other users."})
                else:
                    name_to_deactivate = parameters[0]
                    entity_to_deactivate, found = find_entity_by_name(datastore_client, name_to_deactivate)

                    if found:
                        entity_to_deactivate['active'] = False
                        datastore_client.put(entity_to_deactivate)
                        command_messages.append({'to': from_number, 'body': f"Account for {name_to_deactivate} has been deactivated."})
                    # Optionally notify the deactivated user: send_single_message(entity_to_deactivate.get('phonNbr'), "Your account has been deactivated by an admin.", to_number, all_simulator_messages, twilio_client)
                    else:
                        command_messages.append({'to': from_number, 'body': f"User '{name_to_deactivate}' not found."})
            else:
                command_messages.append({'to': from_number, 'body': "Invalid syntax for +deactivate. Usage: +deactivate [name]"})

        elif command == "status":
            command_messages.append({'to': from_number, 'body': "Service is operational."})
        elif command in ["medic", "armorer", "natloff"]:
            # Command to add or update a user    
            phone_str = None  # Initialize phone_str
            phone_number = None # Initialize phone_number
            if len(parameters) < 2:
                command_messages.append({'to': from_number, 'body': f"Invalid syntax for +{command}. Usage: +{command} [name] [phone]"})
            else:
                name = parameters[0]
                if len(parameters) == 2:
                    phone_str = parameters[1] # Correctly assign the single phone parameter
                elif len(parameters) == 3:
                    # handle phone number of the form (202) 555-1212 where space is a delimiter
                    phone_str = parameters[1] + " " + parameters[2] # Correctly construct the phone string from two parameters
                else:
                    command_messages.append({'to': from_number, 'body': f"Invalid syntax for +{command}. Usage: +{command} [name] [phone]"})
                    # If syntax is invalid, we should not attempt to parse
                    phone_str = None # Ensure phone_str is None if syntax is invalid

                if phone_str: # Attempt to parse the phone number only if phone_str was successfully constructed
                    phone_number = parse_phone_number(phone_str)
            # logger.debug(f"Parsed phone number from command: {phone_number}, format: {phone_format}") # Removed undefined variable phone_format

                if not phone_number:
                    logger.debug(f"Phone number parsing failed for command: {phone_str}")
                    command_messages.append({'to': from_number, 'body': f"Could not parse phone number: {phone_str}. Please use a valid format (e.g., 1234567890, +11234567890, (123) 456-7890, 123-456-7890)."})
                else:
                    entity_by_name, name_found = find_entity_by_name(datastore_client, name)
                    entity_by_number, number_found = find_entity_by_number(datastore_client, phone_number)

                    if name_found:
                        # Name exists
                        stored_phone_number_in_name_entity = entity_by_name.get('phonNbr')
                        # Ensure stored_phone_number_in_name_entity is in a comparable format (with +1 if it's a US number)
                        if stored_phone_number_in_name_entity and len(stored_phone_number_in_name_entity) == 10 and stored_phone_number_in_name_entity.isdigit():
                            stored_phone_number_in_name_entity = '+1' + stored_phone_number_in_name_entity

                        if stored_phone_number_in_name_entity == phone_number:
                            # Name and number match an existing entry, update groups
                            entity_by_name[command] = True
                            entity_by_name['phonNbr'] = phone_number.lstrip('+1')
                            datastore_client.put(entity_by_name)
                            command_messages.append({'to': from_number, 'body': f"{name} with number {phone_number} is now a {command}."})
                            # Notify the user
                            command_messages.append({'to': phone_number, 'body': f"You have been added to the USA Fencing StripCall app as a {command}."})
                        else:
                            # Name exists, number differs. Check if the provided number exists elsewhere.
                            if number_found:
                                # Provided number exists with another name
                                other_name = entity_by_number.get('name')
                                command_messages.append({'to': from_number, 'body': f"Error: That telephone number is associated with {other_name}."})
                            else:
                                # Name exists, numbers differ, new number not found elsewhere. Update the existing record's phone number and groups.
                                entity_by_name[command] = True
                                entity_by_name['phonNbr'] = phone_number.lstrip('+1')
                                datastore_client.put(entity_by_name)
                                command_messages.append({'to': from_number, 'body': f"{name} with new number {phone_number} is now a {command}."})
                                # Notify the user
                                command_messages.append({'to': phone_number, 'body': f"You have been added to the USA Fencing StripCall app as a {command}."})
                    else:
                        # No entity found with the same name. Check if the provided number exists elsewhere.
                        if number_found:
                            # Provided number exists with another name
                            other_name = entity_by_number.get('name')
                            command_messages.append({'to': from_number, 'body': f"Error: That telephone number is associated with {other_name}."})
                        else:
                            # Neither name nor number exists. Create a new record.
                            logger.debug(f"Neither name '{name}' nor number '{phone_number}' found. Creating new entity.")
                            key = datastore_client.key('numbr')
                            new_entity = datastore.Entity(key)
                            new_entity.update({
                                'phonNbr': phone_number.lstrip('+1'),
                                'name': name,
                                'armorer': command == 'armorer',
                                'ref': False, # Assuming 'ref' is not set by these commands
                                'super': False, # Assuming 'super' is not set by these commands
                                'active': True, # Assuming new entries are active
                                'admin': False, # Assuming 'admin' is not set by these commands
                                'ucName': name.upper(), # Assuming uppercase name for ucName
                                'medic': command == 'medic',
                                'natloff': command == 'natloff'
                            })
                            datastore_client.put(new_entity)
                            logger.debug(f"New entity created for {name} with number {phone_number}")
                            command_messages.append({'to': from_number, 'body': f"{name} with number {phone_number} is now a {command}`."})
                            command_messages.append({'to': phone_number, 'body': f"You have been added to the USA Fencing StripCall app as a {command}."})
        elif command == "ref":
            if len(parameters) != 1:
                command_messages.append({'to': from_number, 'body': "Invalid syntax for +ref. Usage: +ref [name]"})
            else:
                name = parameters[0]

                entity_by_name, name_found = find_entity_by_name(datastore_client, name)
                entity_by_number, number_found = find_entity_by_number(datastore_client, from_number)

                if entity_by_name:
                    # Name exists
                    if entity_by_name.get('ref', False):
                        # Entry is a ref
                        stored_phone_number_in_entity = entity_by_name.get('phonNbr')
                        # Need to compare stored number (without +1) with incoming number (with +1 potentially)
                        if stored_phone_number_in_entity != from_number.lstrip('+1'):
                            # Phone number is different, update it
                            entity_by_name['phonNbr'] = from_number.lstrip('+1')
                            datastore_client.put(entity_by_name)
                            command_messages.append({'to': from_number, 'body': f"Phone number updated for ref {name}."})
                        else:
                            # Entry is already present as a ref with the same number
                            command_messages.append({'to': from_number, 'body': f"Entry for {name} is already present as a ref."})
                    else:
                        # Name exists, but not a ref
                        command_messages.append({'to': from_number, 'body': f"Entry for {name} exists but cannot be a ref."})
                else:
                    # Name does not exist. Check if phone number exists with another entity.
                    if entity_by_number:
                         # Phone number exists with a different name
                        other_name = entity_by_number.get('name')
                        command_messages.append({'to': from_number, 'body': f"Phone number already in use with name {other_name}."})
                    else:
                        # Neither name nor number exists. Create a new ref entry.
                        logger.debug(f"Neither name '{name}' nor phone number '{from_number}' found. Creating new ref entry.")
                        key = datastore_client.key('numbr')
                        new_entity = datastore.Entity(key)
                        new_entity.update({
                            'phonNbr': from_number.lstrip('+1'),
                            'name': name,
                            'ref': True,
                            'admin': False,
                            'armorer': False,
                            'medic': False,
                            'natloff': False,
                            'super': False,
                            'active': True, # Assuming new entries are active
                            'ucName': name.upper() # Assuming uppercase name for ucName
                        })
                        datastore_client.put(new_entity)
                        logger.debug(f"New ref entity created for {name}.")
                        command_messages.append({'to': from_number, 'body': f"Ref entry created for {name}."})

        elif command == "remove":
            if len(parameters) != 1:
                command_messages.append({'to': from_number, 'body': "Invalid syntax for +remove. Usage: +remove [name]"})
            elif not is_authorized_command_user:
                command_messages.append({'to': from_number, 'body': "You are not authorized to deactivate other users."})
            else: 
                name = parameters[0]
                entity_to_delete, found = find_entity_by_name(datastore_client, name)

                if found:
                    # Name exists, delete the entity
                    try:
                        datastore_client.delete(entity_to_delete.key)
                        command_messages.append({'to': from_number, 'body': f"Entry for {name} has been removed."})
                    except Exception as e:
                        logger.error(f"Error deleting entity for {name}: {e}")
                        command_messages.append({'to': from_number, 'body': f"An error occurred while trying to remove the entry for {name}."})
                else:
                    # Entry not found
                    command_messages.append({'to': from_number, 'body': f"Entry for {name} not found."})        # list command - requires admin/super
        elif command == "list":
            if len(parameters) > 1: # Allow empty parameter for default list
                command_messages.append({'to': from_number, 'body': "Invalid syntax for +list. Usage: +list [group] (where group is medic, armorer, or natloff)"})
            elif not is_authorized_command_user:
                command_messages.append({'to': from_number, 'body': "You are not authorized to deactivate other users."})
            else: 
                query = datastore_client.query(kind='numbr')
                all_results = list(query.fetch())
                logger.debug(f"All entries: {all_results}")
                group_filter = "armorer" # Default to armorer if no parameter is provided
                if len(parameters) == 1:
                    param = parameters[0].lower()
                    if param in ["medic", "armorer", "natloff", "ref"]: # Added ref to allowed list groups
                        group_filter = param
                        query = datastore_client.query(kind='numbr')
                        query.add_filter(group_filter, '=', True)
                        results = list(query.fetch())
                        logger.debug(f"Number of entries found: {len(results)}")
                        if results:
                            entries = [f"{entity.get('name', 'Unknown')} {entity.get('phonNbr', 'N/A')}" for entity in results]
                            command_messages.append({'to': from_number, 'body': f"List for {group_filter}:" + ", ".join(entries)})
                        else:
                            command_messages.append({'to': from_number, 'body': f"No entries found for {group_filter}."})
                    else:
                        command_messages.append({'to': from_number, 'body': f"Invalid group specified: {param}. Use medic, armorer, natloff, or ref."}) # Improved error message

        elif command == "admin":
        # Grant admin privileges (requires super)
            if not (sender_entity and sender_entity.get('super', False)):
                command_messages.append({'to': from_number, 'body': "You are not authorized to grant admin privileges."})
            elif len(parameters) != 1:
                command_messages.append({'to': from_number, 'body': "Invalid syntax for +admin. Usage: +admin [name]"})
            else:
                name_to_admin = parameters[0]
                query_by_name = datastore_client.query(kind='numbr')
                entity_to_admin, found = find_entity_by_name(datastore_client, name_to_admin)

                if found:
                    entity_to_admin['admin'] = True
                    datastore_client.put(entity_to_admin)
                    command_messages.append({'to': from_number, 'body': f"{name_to_admin} is now an admin."})
                    # Optionally notify the user: send_single_message(entity_to_admin.get('phonNbr'), "You have been granted admin privileges.", to_number, all_simulator_messages, twilio_client)
                else:
                    command_messages.append({'to': from_number, 'body': f"User '{name_to_admin}' not found."})
        elif command == "deadmin":
            # Revoke admin privileges (requires super)
            if not (sender_entity and sender_entity.get('super', False)):
                command_messages.append({'to': from_number, 'body': "You are not authorized to revoke admin privileges."})
            elif len(parameters) != 1:
                command_messages.append({'to': from_number, 'body': "Invalid syntax for +deadmin. Usage: +deadmin [name]"})
            else:
                name_to_deadmin = parameters[0]
                entity_to_deadmin, found = find_entity_by_name(datastore_client, name_to_deadmin)

                if found:
                    entity_to_deadmin['admin'] = False
                    datastore_client.put(entity_to_deadmin)
                    command_messages.append({'to': from_number, 'body': f"{name_to_deadmin} is no longer an admin."})
                else:
                    command_messages.append({'to': from_number, 'body': f"User '{name_to_deadmin}' not found."})

        elif command == "user":
            # Get user details (requires admin/super)
            if not is_authorized_command_user:
                command_messages.append({'to': from_number, 'body': "You are not authorized to view user details."})
            elif len(parameters) != 1:
                command_messages.append({'to': from_number, 'body': "Invalid syntax for +user. Usage: +user [name]"})
            else:
                name_to_find = parameters[0]
                user_entity, found = find_entity_by_name(datastore_client, name_to_find)

                if found:
                    # Pretty-print the entity fields
                    output_message = f"Details for {name_to_find}:\n"
                    for key, value in user_entity.items():
                        output_message += f"{key}: {value}\n"
                    command_messages.append({'to': from_number, 'body': output_message})
                else:
                    command_messages.append({'to': from_number, 'body': f"User '{name_to_find}' not found."})
        # Add command messages to the main list of messages to send
        elif command in ["1", "2", "3", "4"]:
            # Check if the sender is a group member, admin, or super
            is_authorized_plus_command_user = sender_entity and (sender_entity.get(from_group, False) or sender_entity.get('admin', False) or sender_entity.get('super', False))

            if not is_authorized_plus_command_user:
                command_messages.append({'to': from_number, 'body': "You are not authorized to use this command. You must be a group member or admin"})
            else:
                try:
                    # Extract the reply number (1-4) from the command (e.g., +1, +2)
                    reply_num = int(body[1]) # Get the digit after the '+'

                    # Validate that the extracted number is between 1 and 4
                    if 1 <= reply_num <= 4:
                        # Retrieve the recipient's phone number from cb[reply_num]
                        # Ensure the index is within the bounds of the cb list, considering the 1-based index
                        if reply_num < len(cb):
                            recipient_phone_number = cb[reply_num]

                            if recipient_phone_number:
                                # Remove the command (e.g., '+1 ') from the message body
                                modified_body = body[len(command) + 1:].strip() # Remove command and leading/trailing whitespace

                                # Ensure the modified body is not empty after removing the command
                                if modified_body:
                                    logger.debug(f"Sending modified message to group and recipient from cb[{reply_num}] ({recipient_phone_number})")

                                    # Send the modified message to the group
                                    # The sender identity should be the original sender's identity
                                    send_message_to_group(sender_identity, from_group, modified_body, to_number, all_simulator_messages, twilio_client)

                                    # Send the modified message to the recipient whose number is in cb[reply_num]
                                    # Prepend +1 if the target phone number is a 10-digit number
                                    if len(recipient_phone_number) == 10 and recipient_phone_number.isdigit():
                                        recipient_phone_number_e164 = '+1' + recipient_phone_number
                                    else:
                                        recipient_phone_number_e164 = recipient_phone_number

                                    # The from_number for the direct message to the recipient should be the sending Twilio number for the group
                                    send_single_message(recipient_phone_number_e164, modified_body, to_number, all_simulator_messages, twilio_client)
                                else:
                                    command_messages.append({'to': from_number, 'body': "Message body is empty after removing the command."})
                            else:
                                command_messages.append({'to': from_number, 'body': f"No phone number stored for index {reply_num} in the contact list."})
                        else:
                            command_messages.append({'to': from_number, 'body': f"Error accessing contact at index {reply_num}. The contact list might be empty or shorter than expected."})

                except ValueError:
                    # This case should not happen if the command is "+1", "+2", "+3", or "+4", but included for robustness.
                    command_messages.append({'to': from_number, 'body': "Invalid command format."})
                except Exception as e:
                    logger.error(f"Error processing command {command}: {e}", exc_info=True)
                    command_messages.append({'to': from_number, 'body': "An error occurred while processing your request."})

        if command_messages:  # Only process if command generated messages
            for message in command_messages:
                send_single_message(message['to'], message['body'], to_number, all_simulator_messages, twilio_client)
    else: # not a command, it's a broadcast
        # Ensure glbvar_entity is fetched even if it's not a command
        if datastore_client and idx is not None and glbvar_entity is None:
             # Re-fetch glbvar_entity if it wasn't found initially
             logger.warning("glbvar_entity was not found for this group initially, attempting to fetch.")
             query = datastore_client.query(kind='glbvar')
             query.add_filter('idx', '=', idx)
             results = list(query.fetch())
             if results:
                 glbvar_entity = results[0]
                 cbp = glbvar_entity.get('cbp', 1)
                 cb = glbvar_entity.get('cb', [""] * 5)
                 logger.debug(f"Re-fetched glbvar entity for idx {idx}. cbp: {cbp}, cb: {cb}")
             else:
                 logger.error(f"Could not re-fetch glbvar entity for idx {idx}")

        if not sender_is_in_group:
            if glbvar_entity: # Ensure glbvar_entity was fetched for non-commands too
                cbp = cbp + 1
                if cbp > 4:
                    cbp = 1 # Wrap around to 1
                logger.debug(f"Incremented cbp to {cbp} in non-command block")
                # Update cb list with from_number at the new cbp (which is 1-4)
                # Assuming the list 'cb' is meant to be accessed with 1-based indexing corresponding to cbp.
                cb[cbp] = from_number
                cb[cbp] = from_number.lstrip('+1')
                glbvar_entity['cbp'] = cbp
                glbvar_entity['cb'] = cb
                datastore_client.put(glbvar_entity)
                logger.debug(f"Updated glbvar entity: cbp={cbp}, cb={cb} in non-command block")
                body = body + "  +" + str(cbp) + " to reply"
            else:
                logger.error("glbvar_entity not initialized")
        send_message_to_group(sender_identity, from_group, body , to_number, all_simulator_messages, twilio_client)
        if sender_is_ref or not sender_entity:
            send_single_message(from_number, "Got It", to_number, all_simulator_messages, twilio_client)
    if is_sender_simulator:
        return jsonify({'simulator_messages': all_simulator_messages}) # Return collected simulator messages as JSON
    return str(MessagingResponse()) # Return TwiML response

@app.route('/')
def hello_world():
    logger.debug(f"Value of datastore_client at start of hello_world: {datastore_client}")
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
            if entity.get("name") == "Brian":
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
    logger.debug(f"Received request for /get_name with args: {request.args}")
    phone_number = request.args.get('phoneNumber')
 
    # Initialize response_data with default values
    response_data = {
        'name': 'Unknown User',
        'groups': []
    }
    name = None
    groups = []
    found_entity = None
    
    if datastore_client and phone_number: # Check if datastore_client is initialized and phone_number is provided
        found_entity, found = find_entity_by_number(datastore_client, phone_number)

        if found:
            name = found_entity.get('name')
            # Check for group memberships
            if found_entity.get('medic', False):
                groups.append('medic')
            if found_entity.get('armorer', False):
                groups.append('armorer')
            if found_entity.get('natloff', False):
                groups.append('natloff')
            if found_entity.get('admin', False):
                groups.append('admin')
            if found_entity.get('super', False):
                groups.append('super')
            response_data = {'name': name, 'groups': groups}
            logging.debug(f"get_name returned {jsonify(response_data)}")
            return jsonify(response_data) # Return JSON response with name and groups
        else:
            logger.debug("get_name Name not found")
    response_data = {}
    # If no entity was found or phone_number was missing, return an appropriate response
    # Note: If no entity found, name remains None.
    response_data = {'name': name if found_entity else 'Unknown Neme', 'groups': groups} # Set name to 'Unknown User' if entity not found, otherwise use found name
    return jsonify(response_data)
