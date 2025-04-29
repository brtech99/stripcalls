import logging
import os
from google.appengine.ext import webapp
from google.appengine.ext.webapp.util import run_wsgi_app
from google.appengine.ext import db
from google.appengine.api import mail
from twilio.util import RequestValidator

#Data object stored in Datastore
class numbr(db.Model): 
    phonNbr = db.StringProperty(required=True)
    name = db.StringProperty(required=True)
    admin = db.BooleanProperty(indexed=True, default=False)
    armorer = db.BooleanProperty(indexed=True, default=False)
    ref = db.BooleanProperty(indexed=True, default=False)
    super= db.BooleanProperty(indexed=True, default=False)
    active=db.BooleanProperty(default=True)
    ucName=db.StringProperty(indexed=True)
 
class glbvar(db.Model):
    idx = db.IntegerProperty(indexed=True, default=True)
    cbp = db.IntegerProperty(default=False)
    cb = db.ListProperty(str)
    
def dbg(st,slf):
    slf.response.out.write("<dbg " + st + "/>")
    
def printList(): #create a string containing the list of entries in the database
    r=""
    allNum=db.GqlQuery("SELECT * FROM numbr")
    for n in allNum.run(limit=20):
        r=r+n.name+':'+n.phonNbr+" arm="+str(n.armorer)+" ref="+str(n.ref)+" adm="+str(n.admin)+" act="+str(n.active)+", "
    return r

def SendToGroup(sender, body): #create a response string which will send the body parameter to all armorers, 
#                               but not to the sender
    armorers = db.GqlQuery("SELECT * FROM numbr WHERE armorer = TRUE AND phonNbr != '"+sender+"' AND active = TRUE")
    rtnStr = ""
    for n in armorers.run(limit=20): # the 20 is an arbitraty limit, just to prevent a runaway
        rtnStr = rtnStr + '<Sms to="' + n.phonNbr + '">' + body + '</Sms>'
    return rtnStr
    
def parseToken(st, i): #return the next token starting at the ith character
    tok=None
    if i>len(st):
        return
    while st[i] == " ": #skip leading spaces
        i=i+1
    j=st.find(" ",i) #tokens are space delimited
    if j==-1: #no space?  then return the balance of st
        tok=st[i:len(st)]
    else: #found the space
        tok=st[i:j]
    return tok

def validate(pNum): #validate a telephone number
    if len(pNum)==10 and pNum.isdigit:  #only allow 10 digits
        return True
    else:
        return False
   
class MainPage(webapp.RequestHandler): #sonething for the curious
 
    def get(self):
        self.response.headers['Content-Type'] = 'text/html'
        self.response.out.write('<html><body><p>USFA Armory Tools</p>')
        self.response.out.write('<p>Maintained by Brian Rosen</p></body></html>')

class Init(webapp.RequestHandler): #A way to initialize the database, requires admin login
 
    def get(self):
        allNum=db.GqlQuery("SELECT * FROM numbr")
        for n in allNum.run(): #delete all existing entries
            n.delete()
        allGlb=db.GqlQuery("SELECT * FROM glbvar")
        for n in allGlb.run(): #delete all existing entries
            n.delete()
        n=numbr(phonNbr="7246122359", name="Brian", ucName= 'BRIAN', armorer=True, admin=True, super=True)
        n.put() #set up the super-user
        n=glbvar(idx=1, cbp=0, cb=['0','0','0','0', '0'])
        n.put() #set up globals (circular buffer)
        self.response.headers['Content-Type'] = 'text/html'
        self.response.out.write('<html><body><p>ok</p></html>')
     
class ReceiveSMS(webapp.RequestHandler):
    # Handle received SMS
    
    def get(self):
        def write(s): #defining inside the get class allows us to use self, prints line to output page and logger
            self.response.out.write(s)
            logging.debug(s)
            
        try:
    #        if True:
            gbl = db.GqlQuery("SELECT * FROM glbvar WHERE idx = 1")
            g=gbl.get()
            self.response.headers['Content-Type'] = 'text/xml'  #return XML to Twilio
            write('<?xml version="1.0" encoding="UTF-8"?>')
            fromVal = self.request.get('From').encode('ascii', 'ignore') #get phone num of sender
            smsSid = self.request.get('SmsSid').encode('ascii', 'ignore') #get SMS Id
            body = self.request.get('Body').encode('ascii', 'ignore') #get body of message
            body=body.translate(None,"'!@#$%^&*")
            auth_token = '530e3c5c4084c062bd6e438873852a92'
            logging.debug(os.environ.get("TWILIO_AUTH_TOKEN"))
            validator = RequestValidator(auth_token) #set up to check signature using our auth token
            url = "http://usfa-armory.appspot.com/rsms?"+self.request.query_string #create the string
            twilio_signature = self.request.headers.get("X-Twilio-Signature") #get the signature from the header
            sigValid = validator.validate(url, {}, twilio_signature) #validate signature
            if os.environ['SERVER_SOFTWARE'].startswith('Development'): #if this is the development server
                sigValid=True #then we don't get a signature, so believe it's valid
                ts="None"
            if twilio_signature <> None: #this is just for debugging
                ts=twilio_signature
            logging.debug('from=%s sig=%s Validator=%s URL=%s' % (fromVal,ts, sigValid,url))
            if fromVal==None or smsSid==None or sigValid==False: #So, if no from, no sid or bad signature
                write('<!-- So Sad, Too Bad -->') #thats all she wrote
            else:
                write('<Response>') #valid response
                fromVal=fromVal.lstrip('+') #get rid of any leading plus
                fromVal=fromVal.lstrip('1') #get rid of any leading country code
                foundIt = db.GqlQuery("SELECT * FROM numbr WHERE phonNbr = '" + fromVal + "'") #lookup the from
                mbr=foundIt.get()
                if (body != None) and body[0] == '+': # This is a command
                    cmd = None
                    nam = None
                    cmd = parseToken(body,1) #get the command token
                    if cmd != None:
                        nam=parseToken(body,len(cmd)+2) #next token is usually a name
                    if cmd=="armorer":# Armorer - add this number as an armorer            
                        if (mbr == None) or not mbr.admin: #attempt to add oneself as armorer
                            foundIt=db.GqlQuery("SELECT * FROM numbr WHERE ucName = '" + nam.upper() + "'")
                            pMbr = foundIt.get()
                            if pMbr <> None: ##If we already have this name in the db
                                write('<Sms>Already have an entry with that name</Sms>')
                            else: #new number
                                n=numbr(phonNbr=fromVal, name=nam, ucName=nam.upper(),armorer=True, ref=False, active=False)
                                n.put()
                                write('<Sms>%s added as armorer with phone number %s. Requires head tech to activate</Sms>' % (nam, fromVal))
                                foundIt=db.GqlQuery("SELECT * FROM numbr WHERE admin = TRUE")
                                for m in foundIt.run():
                                    aNum = m.phonNbr
                                    write('<Sms to="%s">%s has been added to the armorers list, please activate</Sms>' % (aNum, nam))
                        else:
                            #in the below, the "3" limits us to ONE whitespace between tokens, not good
                            pNum = parseToken(body, len(cmd)+len(nam)+3) #3rd token is phone number
                            if pNum == None or len(pNum)<>10 or not validate(pNum):
                                write("<Sms>No number or bad phone number for %s, no action taken</Sms>" % nam)
                            else:
                                foundIt=db.GqlQuery("SELECT * FROM numbr WHERE phonNbr = '" + pNum + "'")
                                pMbr = foundIt.get()
                                if pMbr <> None: ##If we already have this number in the db
                                    pMbr.armorer=True #just set his armorer flag
                                    pMbr.ref=False #and remove any ref flag
                                    pMbr.put()
                                else: #new number
                                    n=numbr(phonNbr=pNum, name=nam, ucName=nam.upper(),armorer=True, ref=False)
                                    n.put()
                                write('<Sms>%s added as armorer with phone number %s</Sms>' % (nam, pNum))
                                write('<Sms to="%s">You have been added to the armorers list as %s</Sms>' % (pNum, nam))
                    elif cmd=="remove":# Remove - remove this number from the list
                        if (mbr == None) or not mbr.admin:
                            write('<Sms>You are not authorized for that command</Sms>')
                        else:
                            foundIt=db.GqlQuery("SELECT * FROM numbr WHERE ucName = '" + nam.upper() + "'")
                            pMbr = foundIt.get()
                            if pMbr == None: #do we have this number already?
                                write('<Sms>No record found with name="' + nam + '"</Sms>')
                            else: #new number
                                pNum=pMbr.phonNbr
                                pMbr.delete()
                                write('<Sms>%s with phone number %s was deleted from the database</Sms>' % (nam, pNum))
                                write('<Sms to="%s">%s, you have been removed from the armorer database</Sms>' % (pNum, nam))
                            
                    elif cmd=="ref":# Ref - add sender as a ref
                        foundIt=db.GqlQuery("SELECT * FROM numbr WHERE phonNbr = '" + fromVal + "'")
                        pMbr = foundIt.get()
                        if pMbr != None: #do we have this number already?
                            pMbr.name = nam
                            pMbr.ucName = nam.upper()
                            pMbr.ref = True
                            pMbr.put() 
                        else: #new number
                            foundIt=db.GqlQuery("SELECT * FROM numbr WHERE ucName = '" + nam.upper() + "'")
                            pMbr = foundIt.get()
                            if pMbr != None: #do we have this name already?
                                oNum = pMbr.phonNbr
                                write('<Sms>%s already exists with phone number %s, replacing</Sms>' % (nam, oNum))
                                pMbr.phonNbr = fromVal
                                pMbr.ref = True
                                pMbr.put()
                            else:
                                n=numbr(phonNbr=fromVal, name=nam, ucName=nam.upper(), ref=True)
                                n.put()
                                write('<Sms>%s added as director with phone number %s</Sms>' % (nam, fromVal))
                    elif (cmd=="to" or cmd=="reply"):# To - send message to the caller, copy the group
                        if (mbr == None) or not mbr.armorer:
                            write('<Sms>You are not authorized for that command</Sms>')
                        else:
                            if len(nam)==10 and nam.isdigit:
                                pNum=nam #use the phone number as the to
                                pMbr = None
                            else: #assune it's a ref name
                                foundIt=db.GqlQuery("SELECT * FROM numbr WHERE ucName = '" + nam.upper() + "' AND ref = TRUE")
                                pMbr = foundIt.get()
                                if pMbr == None:
                                    write('<Sms>No ref with name %s</Sms>' % nam)
                                    pNum=None
                                else:
                                    pNum=pMbr.phonNbr #use the number from the database entry
                                    #in the below, the 4 means we only allow 1 space between tokens, not good
                            if pNum != None:
                                bdStart = len(nam)+4 #start of real body is past the '#to ' and name
                                s=mbr.name + ": " + body[bdStart:len(body)]
                                dest = pNum #dest string is name if available, otherwise number
                                if pMbr != None:
                                    dest = pMbr.name
                                write('<Sms to="'+pNum+'">'+mbr.name+' to '+dest+":"+body[bdStart:len(body)] + "</Sms>")
                                write(SendToGroup(fromVal,s))
                                                        
                    elif cmd=="inactivate":# Inactivate - don't send this number any more messages, but keep it
                        if (mbr == None) or not (mbr.admin or mbr.armorer):
                            write('<Sms>You are not authorized for that command</Sms>')
                        else:
                            if mbr.admin: #Need a name, but can inactiveate anyone
                                if nam == None:
                                    write('<Sms>No name given, need a name if you are an admin</Sms>')
                                else:
                                    foundIt=db.GqlQuery("SELECT * FROM numbr WHERE ucName = '" + nam.upper() + "'")
                                    pMbr = foundIt.get()
                                if nam == None or pMbr == None: #do we have this name?
                                    write('<Sms>No record found with name="' + nam + '"</Sms>')
                                else: 
                                    pMbr.active=False
                                    pMbr.put() 
                                    write('<Sms>%s inactivated</Sms>' % nam)
                            else: #armorer inactivates himself only
                                if nam == None or len(nam)<=1:
                                    mbr.active=False
                                    mbr.put()
                                    write('<Sms>You are inactivated</Sms>')
                                else:
                                    write('<Sms>You are only allowed to inactivate yourself</Sms>')
                    elif cmd=="activate":# Activate - resume sending this number messages
                        if (mbr == None) or not (mbr.admin or mbr.armorer):
                            write('<Sms>You are not authorized for that command</Sms>')
                        else:
                            if mbr.admin:
                                if nam == None:
                                    write('<Sms>No name given, need a name if you are an admin</Sms>')
                                else:
                                    foundIt=db.GqlQuery("SELECT * FROM numbr WHERE ucName = '" + nam.upper() + "'")
                                    pMbr = foundIt.get()
                                if nam == None or pMbr == None: #do we have this name?
                                    write('<Sms>No record found with name="' + nam + '"</Sms>')
                                else: 
                                    pMbr.active=True
                                    pMbr.put() 
                                    write('<Sms>%s activated</Sms>' % nam)
                            else:
                                if nam == None or len(nam)<=1:
                                    mbr.active=True
                                    mbr.put()
                                    write('<Sms>You are activated</Sms>')
                                else:
                                    write('<Sms>You are only allowed to activate yourself</Sms>')
                    elif cmd=="admin":# Admin - make this number an admin
                        if (mbr == None) or not mbr.super:
                            write('<Sms>You are not authorized for that command</Sms>')
                        else:
                            foundIt=db.GqlQuery("SELECT * FROM numbr WHERE ucName = '" + nam.upper() + "'")
                            pMbr = foundIt.get()
                            if pMbr == None: #do we have this name?
                                write('<Sms>No record found with name="' + nam + '"</Sms>')
                            else: 
                                pMbr.admin=True
                                pMbr.put() 
                                write('<Sms>%s is now an admin</Sms>' % nam)
                                write('<Sms to="%s">you are now an admin</Sms>' % pMbr.phonNbr)
                    elif cmd=="deadmin":# AdminDelete - remove admin from this number
                        if (mbr == None) or not mbr.super:
                            write('<Sms>You are not authorized for that command</Sms>')
                        else:
                            foundIt=db.GqlQuery("SELECT * FROM numbr WHERE ucName = '" + nam.upper() + "'")
                            pMbr = foundIt.get()
                            if pMbr == None: #do we have this name?
                                write('<Sms>No record found with name="' + nam + '"</Sms>')
                            else: 
                                pMbr.admin=False
                                pMbr.put() 
                                write('<Sms>%s is no longer an admin</Sms>' % nam)
                                write('<Sms to="%s">you are no longer an admin</Sms>' % pMbr.phonNbr)
                    elif cmd=="list":# list current armorers
                        if (mbr == None) or not mbr.admin:
                            write('<Sms>You are not authorized for that command</Sms>')
                        else:
                            foundIt=db.GqlQuery("SELECT * FROM numbr WHERE armorer = TRUE")
                            r = "" 
                            for pMbr in foundIt.run():
                                r = r + pMbr.name + ":" + pMbr.phonNbr + ", "
                            while len(r)>155:
                                write('<Sms>' + r[0:155] + "</Sms>")
                                r = r[155:len(r)]
                            write('<Sms>' + r + "</Sms>")
                    elif cmd=="listref":# list current refs
                        if (mbr == None) or not mbr.admin:
                            write('<Sms>You are not authorized for that command</Sms>')
                        else:
                            foundIt=db.GqlQuery("SELECT * FROM numbr WHERE ref = TRUE")
                            r = "" 
                            for pMbr in foundIt.run():
                                r = r + pMbr.name + ":" + pMbr.phonNbr + ", "
                            while len(r)>155:
                                write('<Sms>' + r[0:155] + "</Sms>")
                                r = r[155:len(r)]
                            write('<Sms>' + r + "</Sms>")
                    elif cmd=="addref":# Add ref as admin            
                        if (mbr == None) or not mbr.admin: #only admin can use this command
                            write('<Sms>You are not authorized for that command</Sms>')
                        else:
                            #in the below, the "3" limits us to ONE whitespace between tokens, not good
                            pNum = parseToken(body, len(cmd)+len(nam)+3) #3rd token is phone number
                            if pNum == None or len(pNum)<>10 or not validate(pNum):
                                write("<Sms>No number or bad phone number for %s, no action taken</Sms>" % nam)
                            else:
                                foundIt=db.GqlQuery("SELECT * FROM numbr WHERE phonNbr = '" + pNum + "'")
                                pMbr = foundIt.get()
                                if pMbr <> None: ##If we already have this number in the db
                                    pMbr.armorer=False #remove armorer flag
                                    pMbr.ref=True #and set ref flag
                                    pMbr.name = nam #name
                                    pMbr.ucName = nam.upper()
                                    pMbr.put()
                                else: #new number
                                    foundIt=db.GqlQuery("SELECT * FROM numbr WHERE ucName = '" + nam.upper() + "'")
                                    pMbr = foundIt.get()
                                    if pMbr != None: #do we have this name already?
                                        oNum = pMbr.phonNbr
                                        write('<Sms>%s already exists with phone number %s, replacing</Sms>' % (nam, oNum))
                                        pMbr.phonNbr = pNum
                                        pMbr.ref = True
                                        pMbr.put()
                                    else:
                                        n=numbr(phonNbr=pNum, name=nam, ucName=nam.upper(),armorer=False, ref=True)
                                        n.put()
                                write('<Sms>%s added as ref with phone number %s</Sms>' % (nam, pNum))
                                write('<Sms to="%s">You have been added to the Strip Call ref list as %s</Sms>' % (pNum, nam))
                                 
                    elif (cmd=="1" or cmd=="2" or cmd=="3" or cmd=="4"): #1-4 are replies to the last 4 messages sent to the group
                        if (mbr == None) or not mbr.armorer:
                            write('<Sms>You are not authorized for that command</Sms>')
                        else:
                            pNum = g.cb[int(cmd)] #get the ith entry in the circular buffer, use as calling number
                            foundIt=db.GqlQuery("SELECT * FROM numbr WHERE phonNbr = '" + pNum + "'") #look for this number in db
                            pMbr = foundIt.get()
                            s=mbr.name + ": " + body[3:len(body)]
                            dest = pNum #start by assuming sender is the number from the buffer
                            if pMbr != None:
                                dest = pMbr.name #sender was a ref or armorer, use that as dest
                            write('<Sms to="'+pNum+'">'+mbr.name+' to '+dest+":"+body[3:len(body)] + "</Sms>") #start past the command
                            write(SendToGroup(fromVal,s))                        
                    else: #bad command
                            write('<Sms>Bad command</Sms>')
                else: # This is not a command, send to the group
                    
                    if (mbr <> None): # Is this from an armorer or ref we know?
                        if mbr.armorer:
                            bdy=mbr.name + ": " + body #just send the body
                        else: #it's a ref we know
                            g.cbp=g.cbp + 1 #bump pointer by one
                            if (g.cbp == 5): #wrap around at 4
                                g.cbp = 1
                            g.cb[g.cbp]=fromVal #save phone nbr of sender
                            g.put()
                            bdy=mbr.name + ": " + body + "  +" + str(g.cbp) + " to reply" #send reply prompt
                            write('<Sms to="'+ fromVal+'">Got It</Sms>')
                        rsp=SendToGroup(fromVal,bdy)
                        write(rsp)
                    else: #message from unknown ref 
                        g.cbp=g.cbp + 1
                        if (g.cbp == 5):
                            g.cbp = 1
                        g.cb[g.cbp]=fromVal
                        g.put()
                        bdy=str(fromVal)+":"+ body + "  +" + str(g.cbp) + " to reply" #send reply prompt
                        write('<Sms to="'+ fromVal+'">Got It</Sms>')
                        rsp=SendToGroup(fromVal,bdy)
                        write(rsp)
            logging.debug(printList())
            write('</Response>')
        except (TypeError, ValueError):
#        else:
            self.response.headers['Content-Type'] = 'text/xml'
            write("<Response><Sms>I'm sorry, something went wrong, we'll take a look</Sms></Response>")
            sender_address = "admin@usfa-armory.appspotmail.com"
            recipient_address = "brian.rosen@gmail.com"
            subject = "StripCall Error Report"
            mbody = "FromVal=%s Body=%s Type=%s Value=%s" % (fromVal,body,TypeError, ValueError)
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
