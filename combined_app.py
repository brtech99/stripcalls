import logging
import os

import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore
from google.appengine.ext import webapp
from google.appengine.ext.webapp.util import run_wsgi_app
from google.appengine.ext import db
from google.appengine.api import mail

# Initialize Firestore
# Use the application default credentials
if not firebase_admin._apps: #check to make sure the app isn't already initialized
    cred = credentials.ApplicationDefault()
    firebase_admin.initialize_app(cred, {"projectId": "usfa-armory"})
db_firestore = firestore.client() # get a firestore client
# Data object
class numbr:
    def __init__(self, phonNbr, name, admin=False, armorer=False, ref=False, super=False, active=True, ucName=None, medic=False, natOffice=False):
        self.phonNbr = phonNbr
        self.name = name
        self.admin = admin
        self.armorer = armorer
        self.ref = ref
        self.super = super
        self.active = active
        self.ucName = ucName
        self.medic = medic
        self.natOffice = natOffice

        self.ARMORER_GROUP_NUMBER = "1112223333" #Should be set to the phone number of the armorer group
        self.MEDIC_GROUP_NUMBER = "4445556666" #Should be set to the phone number of the medic group
        self.NAT_OFFICE_GROUP_NUMBER = "7778889999" #Should be set to the phone number of the natOffice group

    def to_dict(self):
        return {
            'phonNbr': self.phonNbr,
            'name': self.name,
            'admin': self.admin,
            'armorer': self.armorer,
            'ref': self.ref,
            'super': self.super,
            'active': self.active,
            'ucName': self.ucName,
            'medic': self.medic,
            'natOffice': self.natOffice
        }

    @classmethod
    def from_dict(cls, document):
        return cls(
            phonNbr=document.get('phonNbr'),
            name=document.get('name'),
            admin=document.get('admin', False),
            armorer=document.get('armorer', False),
            ref=document.get('ref', False),
            super=document.get('super', False),
            active=document.get('active', True),
            ucName=document.get('ucName'),
            medic=document.get('medic', False),
            natOffice=document.get('natOffice', False)
        )

class glbvar:
    def __init__(self, idx=1, cbp=0, cb=None):
        self.idx = idx
        self.cbp = cbp
        self.cb = cb if cb is not None else ['0', '0', '0', '0', '0']

def dbg(st, slf):
    slf.response.out.write("<dbg " + st + "/>")


def printList():
    r = ""
    docs = db_firestore.collection('numbr').stream()
    for doc in docs:
        n = numbr.from_dict(doc.to_dict())
    for n in []:
        r = r + n.name + ':' + n.phonNbr + " med=" + str(n.medic) + " arm=" + str(n.armorer) + " nof=" + str(n.natOffice) + " ref=" + str(n.ref) + " adm=" + str(n.admin) + " act=" + str(n.active) + ", "
    return r

def SendToGroup(sender, body, groupType):  # create a response string which will send the body parameter to all armorers or medics,
        # but not to the sender
    if groupType == "medic":
        query = db_firestore.collection('numbr').where('medic', '==', True).where('phonNbr', '!=', sender).where('active', '==', True).stream()
        group = []
        for doc in query:
            group.append(numbr.from_dict(doc.to_dict()))
    elif groupType == "armorer":
        query = db_firestore.collection('numbr').where('armorer', '==', True).where('phonNbr', '!=', sender).where('active', '==', True).stream()
        group = []
        for doc in query:
            group.append(numbr.from_dict(doc.to_dict()))

    elif groupType == "natoffice":
        query = db_firestore.collection('numbr').where('natOffice', '==', True).where('phonNbr', '!=', sender).where('active', '==', True).stream()
        group = []
        for doc in query:
            group.append(numbr.from_dict(doc.to_dict()))

    else:
        return "" # Return empty string if no group type is given.
    rtnStr = ""
    for n in []: #group.run(limit=20):  # the 20 is an arbitraty limit, just to prevent a runaway
        for member in group:
          rtnStr = rtnStr + '<Sms to="' + member.phonNbr + '">' + body + '</Sms>'
    return rtnStr

def parseToken(st, i):  # return the next token starting at the ith character
    tok = None
    if i > len(st):
        return
    while st[i] == " ":  # skip leading spaces
        i = i + 1
    j = st.find(" ", i)  # tokens are space delimited
    if j == -1:  # no space?  then return the balance of st
        tok = st[i:len(st)]
    else:  # found the space
        tok = st[i:j]
    return tok

def validate(pNum):  # validate a telephone number
    if len(pNum) == 10 and pNum.isdigit():  # only allow 10 digits
        return True
    else:
        return False

class MainPage(webapp.RequestHandler):  # sonething for the curious
    def get(self):
        self.response.headers['Content-Type'] = 'text/html'
        self.response.out.write('<html><body><p>USFA Armory Tools</p>')
        self.response.out.write('<p>Maintained by Brian Rosen</p></body></html>')

class Init(webapp.RequestHandler):  # A way to initialize the database, requires admin login
    def get(self):
        # Delete all documents in the 'numbr' collection
        docs = db_firestore.collection('numbr').stream()
        for doc in docs:
            doc.reference.delete()

        # Delete all documents in the 'glbvar' collection
        docs = db_firestore.collection('glbvar').stream()
        for doc in docs:
            doc.reference.delete()
        
        #allNum = db.GqlQuery("SELECT * FROM numbr")
        #for n in allNum.run():  # delete all existing entries
        #    n.delete()
        #allGlb = db.GqlQuery("SELECT * FROM glbvar")
        #for n in allGlb.run():  # delete all existing entries
        #    n.delete()
        n = numbr(phonNbr="7246122359", name="Brian", ucName='BRIAN', admin=True, super=True) # set up the super-user
        doc_ref = db_firestore.collection('numbr').document()
        doc_ref.set(n.to_dict())
        n = glbvar(idx=1, cbp=0, cb=['0', '0', '0', '0', '0']) # set up globals (circular buffer)
        doc_ref = db_firestore.collection('glbvar').document()
        doc_ref.set({"idx": n.idx, "cbp": n.cbp, "cb": n.cb})
        self.response.headers['Content-Type'] = 'text/html'
        self.response.out.write('<html><body><p>ok</p></html>')

class ReceiveSMS(webapp.RequestHandler):
    # Handle received SMS

    def get(self):
        def write(s):  # defining inside the get class allows us to use self, prints line to output page and logger
            self.response.out.write(s)
            logging.debug(s)

        try:
            docs = db_firestore.collection('glbvar').where('idx', '==', 1).stream()
            g = None
            for doc in docs:
                g = glbvar(idx = doc.get("idx"), cbp = doc.get("cbp"), cb = doc.get("cb"))

            self.response.headers['Content-Type'] = 'text/xml'  # return XML to Twilio
            write('<?xml version="1.0" encoding="UTF-8"?>')
            fromVal = self.request.get('From').encode('ascii', 'ignore')  # get phone num of sender
            smsSid = self.request.get('SmsSid').encode('ascii', 'ignore')  # get SMS Id
            body = self.request.get('Body').encode('ascii', 'ignore')  # get body of message
            body = body.translate(None, "'!@#$%^&*")
            logging.debug(os.environ.get("TWILIO_AUTH_TOKEN"))
            # validator = RequestValidator(auth_token) #set up to check signature using our auth token
            url = "http://usfa-armory.appspot.com/rsms?" + self.request.query_string  # create the string
            twilio_signature = self.request.headers.get("X-Twilio-Signature")  # get the signature from the header
            sigValid = True  # validator.validate(url, {}, twilio_signature) #validate signature
            if os.environ['SERVER_SOFTWARE'].startswith('Development'):  # if this is the development server
                sigValid = True  # then we don't get a signature, so believe it's valid
                ts = "None"
            if twilio_signature <> None:  # this is just for debugging
                ts = twilio_signature
            logging.debug('from=%s sig=%s Validator=%s URL=%s' % (fromVal, ts, sigValid, url))
            if fromVal == None or smsSid == None or sigValid == False:  # So, if no from, no sid or bad signature
                write('<!-- So Sad, Too Bad -->')  # thats all she wrote
            else:
                write('<Response>')  # valid response
                fromVal = fromVal.lstrip('+')  # get rid of any leading plus
                fromVal = fromVal.lstrip('1')  # get rid of any leading country code
                query = db_firestore.collection('numbr').where('phonNbr', '==', fromVal).stream()
                mbr = None
                for doc in query:
                    mbr = numbr.from_dict(doc.to_dict())


                if (body != None) and body[0] == '+':  # This is a command
                    cmd = None
                    nam = None
                    cmd = parseToken(body, 1)  # get the command token
                    if cmd != None:
                        nam = parseToken(body, len(cmd) + 2)  # next token is usually a name
                    #**********************************************************************************************************
                    #***** Start of MEDIC only commands
                    if cmd == "medic":  # Medic - add this number as an medic
                        if (mbr == None) or not mbr.admin:  # attempt to add oneself as medic
                            query = db_firestore.collection('numbr').where('ucName', '==', nam.upper()).stream()
                            pMbr = None
                            for doc in query:
                                pMbr = numbr.from_dict(doc.to_dict())

                            if pMbr <> None:  ##If we already have this name in the db
                                write('<Sms>Already have an entry with that name</Sms>')
                            else:  # new number
                                n = numbr(phonNbr=fromVal, name=nam, ucName=nam.upper(), medic=True, ref=False, active=False) # create new numbr
                                doc_ref = db_firestore.collection('numbr').document() #create new numbr document
                                doc_ref.set(n.to_dict()) #store the dictionary in firestore

                                write('<Sms>%s added as medic with phone number %s. Requires head tech to activate</Sms>' % (nam, fromVal))
                                query = db_firestore.collection('numbr').where('admin', '==', True).stream()
                                foundIt = []
                                for doc in query:
                                    foundIt.append(numbr.from_dict(doc.to_dict()))
                                for m in foundIt:
                                    aNum = m.phonNbr
                                    write('<Sms to="%s">%s has been added to the medic list, please activate</Sms>' % (aNum, nam))
                        else:
                            # in the below, the "3" limits us to ONE whitespace between tokens, not good
                            pNum = parseToken(body, len(cmd) + len(nam) + 3)  # 3rd token is phone number
                            if pNum == None or len(pNum) <> 10 or not validate(pNum):
                                write("<Sms>No number or bad phone number for %s, no action taken</Sms>" % nam)
                            else:                                
                                query = db_firestore.collection('numbr').where('phonNbr', '==', pNum).stream()
                                foundIt = []
                                for doc in query:
                                    foundIt.append(numbr.from_dict(doc.to_dict()))
                                pMbr = None
                                for p in foundIt:
                                    pMbr = p
                                    break
                                
                                if pMbr <> None:  ##If we already have this number in the db
                                    pMbr.medic = True  # just set his medic flag
                                    pMbr.ref = False  # and remove any ref flag
                                    doc_ref = db_firestore.collection('numbr').document() #create new numbr document
                                    doc_id = doc_ref.id #get the unique id of the new document
                                    doc_ref = db_firestore.collection('numbr').document(doc_id) #get the document with the unique ID
                                    doc_ref.set(pMbr.to_dict()) #store the dictionary in firestore
                                else:  # new number
                                    n = numbr(phonNbr=pNum, name=nam, ucName=nam.upper(), medic=True, ref=False)# create new numbr
                                    doc_ref = db_firestore.collection('numbr').document() #create new numbr document
                                    doc_ref.set(n.to_dict()) #store the dictionary in firestore

                                write('<Sms>%s added as medic with phone number %s</Sms>' % (nam, pNum))
                                write('<Sms to="%s">You have been added to the USA Fencing medic list as %s</Sms>' % (pNum, nam))
                    #***** End of MEDIC only commands
                    #**********************************************************************************************************
                    #***** Start of NAT OFFICE only commands
                    elif cmd == "natoffice":  # NatOffice - add this number as an NatOffice
                        if (mbr == None) or not (mbr.admin or mbr.super):  # attempt to add oneself as NatOffice
                            query = db_firestore.collection('numbr').where('ucName', '==', nam.upper()).stream()
                            pMbr = None
                            for doc in query:
                                pMbr = numbr.from_dict(doc.to_dict())

                            if pMbr <> None:  ##If we already have this name in the db
                                write('<Sms>Already have an entry with that name</Sms>')
                            else:  # new number
                                n = numbr(phonNbr=fromVal, name=nam, ucName=nam.upper(), natOffice=True, ref=False, active=False) # create new numbr
                                doc_ref = db_firestore.collection('numbr').document() #create new numbr document
                                doc_ref.set(n.to_dict()) #store the dictionary in firestore

                                write('<Sms>%s added as NatOffice with phone number %s. Requires head tech to activate</Sms>' % (nam, fromVal))
                                query = db_firestore.collection('numbr').where('admin', '==', True).stream()
                                foundIt = []
                                for doc in query:
                                    foundIt.append(numbr.from_dict(doc.to_dict()))
                                for m in foundIt:
                                    aNum = m.phonNbr
                                    write('<Sms to="%s">%s has been added to the National Office list, please activate</Sms>' % (aNum, nam))
                        else:
                            # in the below, the "3" limits us to ONE whitespace between tokens, not good
                            pNum = parseToken(body, len(cmd) + len(nam) + 3)  # 3rd token is phone number
                            if pNum == None or len(pNum) <> 10 or not validate(pNum):
                                write("<Sms>No number or bad phone number for %s, no action taken</Sms>" % nam)
                            else:                                
                                query = db_firestore.collection('numbr').where('phonNbr', '==', pNum).stream()
                                pMbr = None
                                for doc in query:
                                    pMbr = numbr.from_dict(doc.to_dict())
                                if pMbr <> None:  ##If we already have this number in the db
                                    pMbr.natOffice = True  # just set his natOffice flag
                                    doc_ref = db_firestore.collection('numbr').document(doc.id) #get the document with the unique ID
                                    doc_ref.set(pMbr.to_dict()) #store the dictionary in firestore
                                else:  # new number
                                    n = numbr(phonNbr=pNum, name=nam, ucName=nam.upper(), natOffice=True, ref=False)# create new numbr
                                    doc_ref = db_firestore.collection('numbr').document() #create new numbr document
                                    doc_ref.set(n.to_dict()) #store the dictionary in firestore
                                write('<Sms>%s added as NatOffice with phone number %s</Sms>' % (nam, pNum))
                                write('<Sms to="%s">You have been added to the National Office list as %s</Sms>' % (pNum, nam))
                    #**********************************************************************************************************
                    #**********************************************************************************************************
                    #***** Start of ARMORY only commands
                    elif cmd == "armorer":  # Armorer - add this number as an armorer
                        if (mbr == None) or not mbr.admin:  # attempt to add oneself as armorer
                            query = db_firestore.collection('numbr').where('ucName', '==', nam.upper()).stream()
                            pMbr = None
                            for doc in query:
                                pMbr = numbr.from_dict(doc.to_dict())

                            if pMbr <> None:  ##If we already have this name in the db
                                write('<Sms>Already have an entry with that name</Sms>')
                            else:  # new number
                                n = numbr(phonNbr=fromVal, name=nam, ucName=nam.upper(), armorer=True, ref=False, active=False) # create new numbr
                                doc_ref = db_firestore.collection('numbr').document() #create new numbr document
                                doc_ref.set(n.to_dict()) #store the dictionary in firestore

                                write('<Sms>%s added as armorer with phone number %s. Requires head tech to activate</Sms>' % (nam, fromVal))
                                query = db_firestore.collection('numbr').where('admin', '==', True).stream()
                                foundIt = []
                                for doc in query:
                                    foundIt.append(numbr.from_dict(doc.to_dict()))
                                for m in foundIt:
                                    aNum = m.phonNbr
                                    write('<Sms to="%s">%s has been added to the armorers list, please activate</Sms>' % (aNum, nam))
                        else:
                            # in the below, the "3" limits us to ONE whitespace between tokens, not good
                            pNum = parseToken(body, len(cmd) + len(nam) + 3)  # 3rd token is phone number
                            if pNum == None or len(pNum) <> 10 or not validate(pNum):
                                write("<Sms>No number or bad phone number for %s, no action taken</Sms>" % nam)
                            else:                                
                                query = db_firestore.collection('numbr').where('phonNbr', '==', pNum).stream()
                                foundIt = []
                                for doc in query:
                                    foundIt.append(numbr.from_dict(doc.to_dict()))
                                pMbr = None
                                for p in foundIt:
                                    pMbr = p
                                    break
                                if pMbr <> None:  ##If we already have this number in the db
                                    pMbr.armorer = True  # just set his armorer flag
                                    pMbr.ref = False  # and remove any ref flag
                                    doc_ref = db_firestore.collection('numbr').document() #create new numbr document
                                    doc_id = doc_ref.id #get the unique id of the new document
                                    doc_ref = db_firestore.collection('numbr').document(doc_id) #get the document with the unique ID
                                    doc_ref.set(pMbr.to_dict()) #store the dictionary in firestore
                                else:  # new number
                                    n = numbr(phonNbr=pNum, name=nam, ucName=nam.upper(), armorer=True, ref=False) # create new numbr
                                    doc_ref = db_firestore.collection('numbr').document() #create new numbr document
                                    doc_ref.set(n.to_dict()) #store the dictionary in firestore

                                write('<Sms>%s added as armorer with phone number %s</Sms>' % (nam, pNum))
                                write('<Sms to="%s">You have been added to the armorers list as %s</Sms>' % (pNum, nam))
                    #***** End of ARMORY only commands
                    #**********************************************************************************************************
                    elif cmd == "remove":  # Remove - remove this number from the list
                        if (mbr == None) or not mbr.admin:
                            write('<Sms>You are not authorized for that command</Sms>')
                        else:
                            query = db_firestore.collection('numbr').where('ucName', '==', nam.upper()).stream()
                            pMbr = None
                            for doc in query:
                                pMbr = numbr.from_dict(doc.to_dict())

                            if pMbr == None:  # do we have this number already?
                                write('<Sms>No record found with name="' + nam + '"</Sms>')
                            else:  # new number
                                pNum = pMbr.phonNbr
                                db_firestore.collection('numbr').document(doc.id).delete()
                                write('<Sms>%s with phone number %s was deleted from the database</Sms>' % (nam, pNum))
                                if pMbr.medic:                                   
                                    write('<Sms to="%s">%s, you have been removed from the active strip call medic list. You will be added back at the next tournament you work</Sms>' % (pNum, nam))
                                elif pMbr.armorer:
                                    write('<Sms to="%s">%s, you have been removed from the active strip call armorer list. You will be added back at the next tournament you work</Sms>' % (pNum, nam))
                                elif pMbr.natOffice:
                                    write('<Sms to="%s">%s, you have been removed from the active strip call natOffice list. You will be added back at the next tournament you work</Sms>' % (pNum, nam))
                                else:
                                    write('<Sms to="%s">%s, you have been removed from the active strip call database. You will be added back at the next tournament you work</Sms>' % (pNum, nam))

                    elif cmd == "ref":  # Ref - add sender as a ref
                        query = db_firestore.collection('numbr').where('phonNbr', '==', fromVal).stream()
                        pMbr = None
                        for doc in query:
                            pMbr = numbr.from_dict(doc.to_dict())

                        if pMbr != None:  # do we have this number already?
                            pMbr.name = nam
                            pMbr.ucName = nam.upper()
                            pMbr.ref = True
                            doc_ref = db_firestore.collection('numbr').document() #create new numbr document
                            doc_id = doc_ref.id #get the unique id of the new document
                            doc_ref = db_firestore.collection('numbr').document(doc_id) #get the document with the unique ID
                            doc_ref.set(pMbr.to_dict()) #store the dictionary in firestore
                        else:  # new number
                            query = db_firestore.collection('numbr').where('ucName', '==', nam.upper()).stream()
                            pMbr = None
                            for doc in query:
                                pMbr = numbr.from_dict(doc.to_dict())

                            if pMbr != None:  # do we have this name already?
                                oNum = pMbr.phonNbr
                                write('<Sms>%s already exists with phone number %s, replacing</Sms>' % (nam, oNum))
                                pMbr.phonNbr = fromVal
                                pMbr.ref = True
                                doc_ref = db_firestore.collection('numbr').document() #create new numbr document
                                doc_id = doc_ref.id #get the unique id of the new document
                                doc_ref = db_firestore.collection('numbr').document(doc_id) #get the document with the unique ID
                                doc_ref.set(pMbr.to_dict()) #store the dictionary in firestore
                            elif cmd == "tonat": #send a message to the nat office.
                                if (mbr == None) or not (mbr.admin or mbr.super or mbr.natOffice):
                                    write('<Sms>You are not authorized for that command</Sms>')
                                else:
                                    if nam == None:
                                        write('<Sms>No message to send to natoffice</Sms>')
                                    else:
                                        bdStart = len(nam) + 1  # start of real body is past the '#tonat ' and name
                                        s = mbr.name + ": " + body[bdStart:len(body)]
                                        write(SendToGroup(fromVal,s, "natoffice"))

                                doc_ref = db_firestore.collection('numbr').document(doc_id) #get the document with the unique ID
                                doc_ref.set(pMbr.to_dict()) #store the dictionary in firestore
                            else:
                                n = numbr(phonNbr=fromVal, name=nam, ucName=nam.upper(), ref=True) # create new numbr
                                doc_ref = db_firestore.collection('numbr').document() #create new numbr document
                                doc_ref.set(n.to_dict()) #store the dictionary in firestore

                                write('<Sms>%s added as director with phone number %s</Sms>' % (nam, fromVal))
                    elif (cmd == "to" or cmd == "reply"):  # To - send message to the caller, copy the group or nat office
                        if (mbr == None) or not (mbr.medic or mbr.armorer or mbr.natOffice):
                           write('<Sms>You are not authorized for that command</Sms>')
                        else:
                            if len(nam) == 10 and nam.isdigit():
                                pNum = nam  # use the phone number as the to
                                pMbr = None
                            else:  # assune it's a ref name
                                foundIt = db.GqlQuery("SELECT * FROM numbr WHERE ucName = '" + nam.upper() + "' AND ref = TRUE")
                                query = db_firestore.collection('numbr').where('ucName', '==', nam.upper()).where("ref", "==", True).stream()
                                pMbr = None
                                for doc in query:
                                    pMbr = numbr.from_dict(doc.to_dict())

                                if pMbr is None:
                                    write('<Sms>No ref with name %s</Sms>' % nam)
                                    pNum = None
                                else:
                                    pNum = pMbr.phonNbr  # use the number from the database entry
                            if pNum != None:
                                bdStart = len(nam) + 4  # start of real body is past the '#to ' and name
                                s = mbr.name + ": " + body[bdStart:len(body)]
                                dest = pNum  # dest string is name if available, otherwise number
                                if pMbr != None:
                                    dest = pMbr.name
                                write('<Sms to="' + pNum + '">' + mbr.name + ' to ' + dest + ":" + body[bdStart:len(body)] + "</Sms>")
                                if mbr.medic:
                                    write(SendToGroup(fromVal, s, "medic"))
                                elif mbr.natOffice:
                                    write(SendToGroup(fromVal,s, "natoffice"))
                                else:
                                    write(SendToGroup(fromVal,s, "armorer"))
                    elif cmd == "inactivate":  # Inactivate - don't send this number any more messages, but keep it
                        if (mbr == None) or not (mbr.admin or mbr.medic or mbr.armorer):
                            write('<Sms>You are not authorized for that command</Sms>')
                        else:
                            if mbr.admin:  # Need a name, but can inactiveate anyone
                                if nam == None:
                                    write('<Sms>No name given, need a name if you are an admin</Sms>')
                                else:
                                    query = db_firestore.collection('numbr').where('ucName', '==', nam.upper()).stream()
                                    pMbr = None
                                    for doc in query:
                                        pMbr = numbr.from_dict(doc.to_dict())

                                if nam == None or pMbr == None:  # do we have this name?
                                    write('<Sms>No record found with name="' + nam + '"</Sms>')
                                else:
                                    pMbr.active = False
                                    doc_ref = db_firestore.collection('numbr').document() #create new numbr document
                                    doc_id = doc_ref.id #get the unique id of the new document
                                    doc_ref = db_firestore.collection('numbr').document(doc_id) #get the document with the unique ID
                                    doc_ref.set(pMbr.to_dict()) #store the dictionary in firestore
                                    write('<Sms>%s inactivated</Sms>' % nam)
                            else:  # medic, natOffice or armorer inactivates himself only
                                if nam == None or len(nam) <= 1:
                                    mbr.active = False
                                    write('<Sms>You are inactivated</Sms>')
                                else:
                                    write('<Sms>You are only allowed to inactivate yourself</Sms>')
                    elif cmd == "activate":  # Activate - resume sending this number messages
                        if (mbr == None) or not (mbr.admin or mbr.medic or mbr.natOffice or mbr.armorer):
                            write('<Sms>You are not authorized for that command</Sms>')
                        else:
                            if mbr.admin:
                                if nam == None:
                                    write('<Sms>No name given, need a name if you are an admin</Sms>')
                                else:
                                    query = db_firestore.collection('numbr').where('ucName', '==', nam.upper()).stream()
                                    pMbr = None
                                    for doc in query:
                                        pMbr = numbr.from_dict(doc.to_dict())

                                if nam == None or pMbr == None:  # do we have this name?
                                    write('<Sms>No record found with name="' + nam + '"</Sms>')
                                else:
                                    pMbr.active = True
                                    doc_ref = db_firestore.collection('numbr').document() #create new numbr document
                                    doc_id = doc_ref.id #get the unique id of the new document
                                    doc_ref = db_firestore.collection('numbr').document(doc_id) #get the document with the unique ID
                                    doc_ref.set(pMbr.to_dict()) #store the dictionary in firestore
                                    write('<Sms>%s activated</Sms>' % nam)
                            else:
                                if nam == None or len(nam) <= 1:
                                    write('<Sms>You are activated</Sms>')
                                else:
                                    write('<Sms>You are only allowed to activate yourself</Sms>')
                    elif cmd == "admin":  # Admin - make this number an admin
                        if (mbr == None) or not mbr.super:
                            write('<Sms>You are not authorized for that command</Sms>')
                        else:
                            query = db_firestore.collection('numbr').where('ucName', '==', nam.upper()).stream()
                            pMbr = None
                            for doc in query:
                                pMbr = numbr.from_dict(doc.to_dict())

                            if pMbr == None:  # do we have this name?
                                write('<Sms>No record found with name="' + nam + '"</Sms>')
                            else:
                                pMbr.admin = True
                                doc_ref = db_firestore.collection('numbr').document() #create new numbr document
                                doc_id = doc_ref.id #get the unique id of the new document
                                doc_ref = db_firestore.collection('numbr').document(doc_id) #get the document with the unique ID
                                doc_ref.set(pMbr.to_dict()) #store the dictionary in firestore
                                write('<Sms>%s is now an admin</Sms>' % nam)
                                write('<Sms to="%s">you are now an admin</Sms>' % pMbr.phonNbr)
                    elif cmd == "deadmin":  # AdminDelete - remove admin from this number
                        if (mbr == None) or not mbr.super:
                            write('<Sms>You are not authorized for that command</Sms>')
                        else:
                            query = db_firestore.collection('numbr').where('ucName', '==', nam.upper()).stream()
                            pMbr = None
                            for doc in query:
                                pMbr = numbr.from_dict(doc.to_dict())

                            if pMbr == None:  # do we have this name?
                                write('<Sms>No record found with name="' + nam + '"</Sms>')
                            else:
                                pMbr.admin = False
                                doc_ref = db_firestore.collection('numbr').document() #create new numbr document
                                doc_id = doc_ref.id #get the unique id of the new document
                                doc_ref = db_firestore.collection('numbr').document(doc_id) #get the document with the unique ID
                                doc_ref.set(pMbr.to_dict()) #store the dictionary in firestore
                                write('<Sms>%s is no longer an admin</Sms>' % nam)
                                write('<Sms to="%s">you are no longer an admin</Sms>' % pMbr.phonNbr)
                    elif cmd[:4] == "list":  # list current medics or armorers
                        if (mbr == None) or not mbr.admin:  # only admins can list people
                            write('<Sms>You are not authorized for that command</Sms>')
                        else:  # user is an admin
                            if len(cmd) == 4: # just the word list
                                query = db_firestore.collection('numbr').where('armorer', '==', True).stream()
                            elif len(cmd) > 4:
                                if cmd[4] == "m":  # list medics
                                    query = db_firestore.collection('numbr').where('medic', '==', True).stream()
                                elif cmd[4] == "a":  # list armorers
                                    query = db_firestore.collection('numbr').where('armorer', '==', True).stream()
                                elif cmd[4] == "n":  # list natOffice
                                    query = db_firestore.collection('numbr').where('natOffice', '==', True).stream()
                                elif cmd[4] == "r":  # list refs
                                    query = db_firestore.collection('numbr').where('ref', '==', True).stream()
                                else:
                                    write("<Sms>Invalid list type</Sms>")
                                    query = None
                            else:
                                write("<Sms>Invalid list type</Sms>")
                                query = None
                            if query != None:
                            foundIt = []
                            for doc in query:
                                foundIt.append(numbr.from_dict(doc.to_dict()))
                            


                            for pMbr in foundIt:
                                r = r + pMbr.name + ":" + pMbr.phonNbr + ", "
                            while len(r) > 155:
                                write('<Sms>' + r[0:155] + "</Sms>")
                                r = r[155:len(r)]
                            write('<Sms>' + r + "</Sms>")
                    elif cmd == "addref":  # Add ref as admin
                        if (mbr == None) or not mbr.admin:  # only admin can use this command
                            write('<Sms>You are not authorized for that command</Sms>')
                        else:
                            # in the below, the "3" limits us to ONE whitespace between tokens, not good
                            pNum = parseToken(body, len(cmd) + len(nam) + 3)  # 3rd token is phone number
                            if pNum == None or len(pNum) <> 10 or not validate(pNum):
                                write("<Sms>No number or bad phone number for %s, no action taken</Sms>" % nam)
                            else:
                                query = db_firestore.collection('numbr').where('phonNbr', '==', pNum).stream()
                                pMbr = None
                                for doc in query:
                                    pMbr = numbr.from_dict(doc.to_dict())

                                if pMbr <> None:  ##If we already have this number in the db
                                    pMbr.medic = False #remove medic flag
                                    pMbr.armorer = False #remove armorer flag
                                    pMbr.ref = True  # and set ref flag
                                    pMbr.name = nam  # name
                                    pMbr.ucName = nam.upper()
                                    doc_ref = db_firestore.collection('numbr').document() #create new numbr document
                                    doc_ref.set(pMbr.to_dict()) #store the dictionary in firestore

                                else:  # new number
                                    query = db_firestore.collection('numbr').where('ucName', '==', nam.upper()).stream()
                                    pMbr = None
                                    for doc in query:
                                        pMbr = numbr.from_dict(doc.to_dict())
                                    if pMbr != None:  # do we have this name already?
                                        oNum = pMbr.phonNbr
                                        write('<Sms>%s already exists with phone number %s, replacing</Sms>' % (nam, oNum))
                                        pMbr.phonNbr = pNum
                                        pMbr.ref = True
                                        doc_ref = db_firestore.collection('numbr').document() #create new numbr document
                                        doc_id = doc_ref.id #get the unique id of the new document
                                        doc_ref = db_firestore.collection('numbr').document(doc_id) #get the document with the unique ID
                                        doc_ref.set(pMbr.to_dict()) #store the dictionary in firestore
                                    else:
                                        n = numbr(phonNbr=pNum, name=nam, ucName=nam.upper(), medic=False, armorer = False, ref=True)# create new numbr
                                        doc_ref = db_firestore.collection('numbr').document() #create new numbr document
                                        doc_ref.set(n.to_dict()) #store the dictionary in firestore

                                write('<Sms>%s added as ref with phone number %s</Sms>' % (nam, pNum))
                                write('<Sms to="%s">You have been added to the Strip Call ref list as %s</Sms>' % (pNum, nam))

                    elif (cmd == "1" or cmd == "2" or cmd == "3" or cmd == "4"):  # 1-4 are replies to the last 4 messages sent to the group
                        if (mbr == None) or not (mbr.medic or mbr.armorer):
                            write('<Sms>You are not authorized for that command</Sms>')
                        else:
                            pNum = g.cb[int(cmd)]  # get the ith entry in the circular buffer, use as calling number
                            query = db_firestore.collection('numbr').where('phonNbr', '==', pNum).stream()
                            pMbr = None
                            for doc in query:
                                pMbr = numbr.from_dict(doc.to_dict())

                            s = mbr.name + ": " + body[3:len(body)]
                            dest = pNum  # start by assuming sender is the number from the buffer
                            if pMbr != None:
                                dest = pMbr.name  # sender was a ref or medic, use that as dest
                            write('<Sms to="' + pNum + '">' + mbr.name + ' to ' + dest + ":" + body[3:len(body)] + "</Sms>")  # start past the command
                            if mbr.medic:
                                write(SendToGroup(fromVal, s,"medic"))
                            else:
                                write(SendToGroup(fromVal,s,"armorer"))
                    else:  # bad command
                        write('<Sms>Bad command</Sms>')
                else:  # Not a command
                    bdy = mbr.name if mbr else str(fromVal)
                    bdy += ":" + body  # Always include the message body

                    add_reply = True  # Flag to determine if "+N to reply" should be added
                    rsp = ""
                    isMedic = False
                    isArmorer = False
                    isNatOffice = False
                    if mbr is None:  # Unregistered user
                        if fromVal == self.MEDIC_GROUP_NUMBER:
                            isMedic = True
                        elif fromVal == self.ARMORER_GROUP_NUMBER:
                            isArmorer = True
                        elif fromVal == self.NAT_OFFICE_GROUP_NUMBER:
                            isNatOffice = True
                    elif mbr:
                        isMedic = mbr.medic
                        isArmorer = mbr.armorer
                        isNatOffice = mbr.natOffice


                    if add_reply and not isNatOffice:
                        bdy += "  +" + str(g.cbp) + " to reply"
                    g.cbp = (g.cbp % 4) + 1
                    #Add to circular buffer
                    g.cb[g.cbp] = fromVal
                    if isMedic:
                         rsp = SendToGroup(fromVal, bdy, "medic")
                    elif isArmorer:
                         rsp = SendToGroup(fromVal, bdy, "armorer")
                    elif isNatOffice:
                         rsp = SendToGroup(fromVal, bdy, "natoffice")
                            if mbr.medic:
                                rsp = SendToGroup(fromVal, bdy, "medic")
                            elif mbr.natOffice:
                                rsp = SendToGroup(fromVal, bdy, "natoffice")
                            else:
                                rsp = SendToGroup(fromVal, bdy, "armorer")
                        else: rsp = SendToGroup(fromVal, bdy, "medic")
                    
                    doc_ref = db_firestore.collection('glbvar').document(doc.id)
                    doc_ref.update({"cbp": g.cbp, "cb": g.cb})
                    write(rsp)
                    if mbr == None: write('<Sms to="' + fromVal + '">Got It</Sms>')

                write('</Response>')
        except (TypeError, ValueError):
                self.response.headers['Content-Type'] = 'text/xml'
                write("<Response><Sms>I'm sorry, something went wrong, we'll take a look</Sms></Response>")
                sender_address = "admin@usfa-armory.appspotmail.com"
                recipient_address = "brian.rosen@gmail.com"
                subject = "StripCall Error Report"
                mbody = "FromVal=%s Body=%s Type=%s Value=%s" % (fromVal, body, TypeError, ValueError)
                mail.send_mail(sender_address, recipient_address, subject, mbody)

application = webapp.WSGIApplication([('/', MainPage),
                                        ('/rsms', ReceiveSMS),
                                        ('/xinit', Init),
                                        ],
                                     debug=True)

def main():
    run_wsgi_app(application)

if __name__ == "__main__":
    main()