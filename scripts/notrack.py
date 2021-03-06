#!/usr/bin/env python3
#Title : NoTrack
#Description : This script will download latest block lists from various sources, then parse them into Dnsmasq
#Author : QuidsUp
#Date : 2015-01-14
#Version : 0.9.5
#Usage : sudo python notrack.py

#Standard imports
import argparse
import os
import re #TODO load_config and is_running still have regular expressions to move to ntrkregex
import shutil
import signal
import stat
import subprocess
import sys
import time

#Local imports
from blocklists import *
from ntrkfolders import FolderList
from ntrkshared import *
from ntrkmariadb import DBWrapper
from ntrkpause import *
from ntrkregex import *
from ntrkservices import Services

#######################################
# Constants
#######################################
MAX_AGE = 172800 #2 days in seconds
CURRENT_TIME = time.time()
FORCE = False


#######################################
# Global Lists / Dictionaries
#######################################
blocklist = []
blockdomiandict = {}
blocktlddict = {}
whitedict = {}

dnsserver_whitelist = ''
dnsserver_blacklist = ''

#######################################
# Global Variables
#######################################
dedupcount = 0
domaincount = 0
totaldedupcount = 0

config = {
    'NetDev' : 'eth0',
    'IPVersion' : 'IPv4',
    'Search' : 'DuckDuckGo',
    'SearchUrl' : 'https://duckduckgo.com/?q=',
    'WhoIs' : 'Who.is',
    'WhoIsUrl' : 'https://who.is/whois/',
    'Username' : '',
    'Password' : '',
    'Delay' : '30',
    'Suppress' : '',
    'ParsingTime' : '4',
    'api_key' : '',
    'api_readonly' : '',
    'bl_custom' : '',                                      #Special processing required
    'blockmessage' : 'pixel',
    'ipaddress' : '127.0.0.1',
    'whoisapi' : '',
    'status' : '1',
    'unpausetime' : '0',
    'autoupgrade' : '0'
}


class Host:
    """
    Host gets the Name and IP address of this system
    If an IP has been supplied then use that instead of trying to find the system IP
    Usecase is for when NoTrack is used on a VPN

    Parameters:
        conf_ip (str): config.ipaddress
    """

    def __init__(self, conf_ip):
        import socket

        self.name = ''
        self.ip = ''

        self.name = socket.gethostname()                   #Host Name is easy to get

        #Has a non-loopback config - ipaddress been supplied?
        if conf_ip == '127.0.0.1' or conf_ip == '::1' or conf_ip == '':
            #Make a network connection to unroutable 169.x IP in order to get system IP
            #Connect to an unroutable 169.254.0.0/16 address on port 1
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("169.254.0.255", 1))
            except OSError as e:
                print('Host Init: Error - Unable to open network connection')
                sys.exit(1)
            else:
                self.ip = s.getsockname()[0]
            finally:
                s.close()
        else:
            self.ip = conf_ip



#End Classes---------------------------------------------------------

def add_blacklist(domain):
    return dnsserver_blacklist % domain

def add_whitelist(domain):
    return dnsserver_whitelist % domain


def str2bool(v):
    """
    Convert string to boolean value

    Parameters:
        v (str): Value to convert
    Returns:
        Boolean result
    """
    return v.lower() in ('1', 'true', 'yes')


def is_running():
    """
    Get current pid and script name
    Run pgrep -a python3 - look for instances of python3 running
    Look through results of above command
    Check for my script name not equalling my pid

    Returns:
        0 no other instances running
        >0 first match of another instance
    """
    Regex_Pid = re.compile('^(\d+)\spython3?\s([\w\.\/]+)')
    mypid = os.getpid()                                    #Current PID
    myname = os.path.basename(__file__)                    #Current Script Name
    cmd = 'pgrep -a python3'

    try:
        res = subprocess.check_output(cmd,shell=True, universal_newlines=True)
    except subprocess.CalledProcessError as e:
        print('error', e.output, e.returncode)
    else:
        for line in res.splitlines():
            matches = Regex_Pid.search(line)
            if matches is not None:
                if matches.group(2).find(myname) and int(matches.group(1)) != mypid:
                    print('%s is already running on pid %s' % (myname, matches.group(1)))
                    return int(matches.group(1))


    return 0


def load_config():
    """
    Load configuration file
    """
    global blocklistconf

    Regex_BLLine = re.compile('^(bl_[a-z_]+)\s+=\s+([01])\n')
    Regex_ConfLine = re.compile('^([\w_]+)\s+=\s+(.+)\n')

    print('Loading Config')
    conflines = read_file(folders.notrack_config)

    for line in conflines:
        matches = Regex_BLLine.search(line)
        if matches is not None:
            if matches.group(1) in blocklistconf:
                blocklistconf[matches.group(1)][0] = str2bool(matches.group(2))

        matches = Regex_ConfLine.search(line)
        if matches is not None:
            if matches.group(1) in config:
                config[matches.group(1)] = matches.group(2)


def save_config():
    """
    Build list from blocklists
    Add old conf to the list
    """
    newconf = []                                           #Temp list for new conf

    for blitem in blocklistconf.items():                   #Add name and enabled from bl
        newconf.append('%s = %d\n' % (blitem[0], int(blitem[1][0])))

    for item, value in config.items():                     #Add item, value from old conf
        newconf.append(item + ' = ' + value + '\n')

    print('Saving config')
    save_list(newconf, folders.notrack_config)             #Save the new config


def read_csv(filename):
    """
    Load contents of csv and return as a list
    1. Check file exists
    2. Read all lines of csv

    Parameters:
        filename (str): File to load
    Returns:
        List of all lines in file
        Empty list if file doesn't exist
    """
    import csv
    data = []

    if not os.path.isfile(filename):
        print('\t%s is missing' % filename)
        return []

    f = open(filename)
    reader = csv.reader(f)
    data = (list(reader))
    f.close()

    return data


def read_file(filename):
    """
    Load contents of file and return as a list
    1. Check file exists
    2. Read all lines of file

    Parameters:
        filename (str): File to load
    Returns:
        List of all lines in file
        Empty list if file doesn't exist
    """
    filelines = []

    if not os.path.isfile(filename):
        print('\t%s is missing' % filename)
        return []

    #print('\tReading data from %s' % filename)
    f = open(filename, 'r')
    filelines = f.readlines()
    f.close()

    return filelines


def save_list(domains, filename):
    """
    Save a list into a file

    Parameters:
        domains (list): list of data to save to file
        filename (str): File to save

    Returns:
        True on success
        False on error
    """
    try:
        f = open(filename, 'w')                            #Open file for ascii writing
    except IOError as e:
        print('Error writing to %s' % filename)
        print(e)
        return False
    except OSError as e:
        print('Error writing to %s' % filename)
        print(e)
        return False
    else:
        f.writelines(domains)
        f.close()
    return True


def extract_list(sourcezip, destination):
    """
    Unzip a file to destination

    Parameters:
        sourcezip (str): Zip file
        destination (str): Output destination
    """
    from zipfile import ZipFile

    with ZipFile(sourcezip) as zipobj:
        for compressedfile in zipobj.namelist():
            if compressedfile.endswith('.txt'):
                zipobj.extract(compressedfile, folders.tempdir)
                print('\tExtracting %s' % compressedfile)
                move_file(folders.tempdir + compressedfile, destination)


def add_domain(subdomain, comment, source):
    """
    Process supplied domain and add it to blocklist
    1. Extract domain.co.uk from say subdomain.domain.co.uk
    2. Check if domain.co.uk is in blockdomiandict
    3. If subdomain is actually a domain then record domain in blockdomiandict
    4. Reverse subdomain
    5. Append to blocklist as [reverse, subdomain, comment, source]

    Parameters:
        subdomain (str): Subdomain or domain
        comment (str): A comment
        source (str): Block list name
    """
    global blocklist, dedupcount, domaincount, totaldedupcount

    reverse = ''

    matches = Regex_Domain.search(subdomain)
    if matches == None:                                    #Shouldn't happen
        return

    if matches.group(0) in blockdomiandict:                #Blocked by domain or whitelisted?
        #print('\t%s is already in blockdomiandict as %s' % (subdomain, matches.group(0)))
        dedupcount += 1
        totaldedupcount += 1
        return

    if matches.group(2) in blocktlddict:                   #Blocked by TLD?
        #print('\t%s is blocked by TLD as %s' % (subdomain, matches.group(2)))
        return

    if matches.group(0) == subdomain:                      #Add domain.co.uk to blockdomiandict
        #print('Adding domain %s' % subdomain)
        blockdomiandict[subdomain] = True

    #Reverse the domain for later sorting and deduplication
    #An Extra dot is required to match other subdomains and avoid similar spellings
    reverse = subdomain[::-1] + '.'

    blocklist.append(tuple([reverse, subdomain, comment, source]))
    domaincount += 1


def match_defanged(line, listname):
    """
    Checks custom blocklist file line against Defanged List line regex

    Parameters:
        line (str): Line from file
        listname (str): Blocklist name
    Returns:
        True on successful match
        False when no match is found
    """
    matches = Regex_Defanged.search(line)                  #Search for first match
    if matches is not None:                                #Has a match been found?
        #Add group 1 - Domain and replace defanged [.] with .
        add_domain(matches.group(1).replace('[.]', '.'), '', listname)
        return True

    return False                                           #Nothing found, return False


def match_easyline(line, listname):
    """
    Checks custom blocklist file line against Easy List line regex

    Parameters:
        line (str): Line from file
        listname (str): Blocklist name
    Returns:
        True on successful match
        False when no match is found
    """
    matches = Regex_EasyLine.search(line)                  #Search for first match
    if matches is not None:                                #Has a match been found?
        add_domain(matches.group(1), '', listname)         #Add group 1 - Domain
        return True

    return False                                           #Nothing found, return False


def match_plainline(line, listname):
    """
    Checks custom blocklist file line against Plain List line regex

    Parameters:
        line (str): Line from file
        listname (str): Blocklist name
    Returns:
        True on successful match
        False when no match is found
    """
    matches = Regex_PlainLine.search(line)                 #Search for first match
    if matches is not None:                                #Has a match been found?
        add_domain(matches.group(1), matches.group(2), listname)
        return True

    return False                                           #Nothing found, return False


def match_unixline(line, listname):
    """
    Checks custom blocklist file line against Unix List line regex

    Parameters:
        line (str): Line from file
        listname (str): Blocklist name
    Returns:
        True on successful match
        False when no match is found
    """
    matches = Regex_UnixLine.search(line)                  #Search for first match
    if matches is not None:                                #Has a match been found?
        add_domain(matches.group(1), matches.group(2), listname)
        return True

    return False                                           #Nothing found, return False


def process_customlist(lines, listname):
    """
    We don't know what type of list this is, so try regex match against different types
    1. Reset dedup and domain counters
    2. Read list of lines
    3. Try different regex matches

    Parameters:
        lines (list): List of lines
        listname (str): Blocklist name
    """
    global dedupcount, domaincount

    dedupcount = 0                                         #Reset per list dedup count
    domaincount = 0                                        #Reset per list domain count

    print('\t%d lines to process' % len(lines))

    for line in lines:                                     #Read through list
        if match_plainline(line, 'custom'):                #Try against Plain line
            continue
        if match_easyline(line, 'custom'):                 #Try agaisnt Easy List
            continue
        if match_unixline(line, 'custom'):                 #Try against Unix List
            continue
        match_defanged(line, 'custom')                     #Finally try against Defanged

    print('\tAdded %d domains' % domaincount)              #Show stats for the list
    print('\tDeduplicated %d domains' % dedupcount)


def process_easylist(lines, listname):
    """
    List of domains in Adblock+ filter format [https://adblockplus.org/filter-cheatsheet]
    1. Reset dedup and domain counters
    2. Read list of lines
    3. Check regex match against Regex_EasyLine
    4. Add domain

    Parameters:
        lines (list): List of lines
        listname (str): Blocklist name
    """
    global dedupcount, domaincount

    dedupcount = 0                                         #Reset per list dedup count
    domaincount = 0                                        #Reset per list domain count

    print('\t%d lines to process' % len(lines))

    for line in lines:                                     #Read through list
        matches = Regex_EasyLine.search(line)              #Search for first match
        if matches is not None:                            #Has a match been found?
            add_domain(matches.group(1), '', listname)     #Add group 1 - Domain

    print('\tAdded %d domains' % domaincount)              #Show stats for the list
    print('\tDeduplicated %d domains' % dedupcount)


def process_plainlist(lines, listname):
    """
    List of domains with optional # separated comments
    1. Reset dedup and domain counters
    2. Read list of lines
    3. Split each line by hash delimiter
    4. Add domain

    Parameters:
        lines (list): List of lines
        listname (str): Blocklist name
    """
    global dedupcount, domaincount
    splitline = []

    dedupcount = 0                                         #Reset per list dedup count
    domaincount = 0                                        #Reset per list domain count

    print('\t%d lines to process' % len(lines))

    for line in lines:                                     #Read through list
        splitline = line.split('#', 1)                     #Split by hash delimiter
        if splitline[0] == '\n' or splitline[0] == '':     #Ignore Comment line or Blank
            continue

        if len(splitline) > 1:                             #Line has a comment
            add_domain(splitline[0][:-1], splitline[1][:-1], listname)
        else:                                              #No comment, leave it blank
            add_domain(splitline[0][:-1], '', listname)

    print('\tAdded %d domains' % domaincount)              #Show stats for the list
    print('\tDeduplicated %d domains' % dedupcount)


def process_unixlist(lines, listname):
    """
    List of domains starting with either 0.0.0.0 or 127.0.0.1 domain.com
    1. Reset dedup and domain counters
    2. Read list of lines
    3. Check regex match against Regex_UnixLine
    4. Add domain

    Parameters:
        lines (list): List of lines
        listname (str): Blocklist name
    """
    global dedupcount, domaincount

    dedupcount = 0                                         #Reset per list dedup count
    domaincount = 0                                        #Reset per list domain count

    print('\t%d lines to process' % len(lines))

    for line in lines:                                     #Read through list
        matches = Regex_UnixLine.search(line)              #Search for first match
        if matches is not None:                            #Has a match been found?
            add_domain(matches.group(1), '', listname)     #Add group 1 - Domain

    print('\tAdded %d domains' % domaincount)              #Show stats for the list
    print('\tDeduplicated %d domains' % dedupcount)


def process_tldlist():
    """
    Load users black & white tld lists
    Load NoTrack provided tld csv
    Create blocktlddict from high risk tld not in whitelist and low risk tld in blacklist
    Check for any domains in users whitelist that would be blocked by tld
    Save whitelist of domains from previous step
    """
    global blocklist, blocktlddict, domaincount

    #local name=""
    #local risk=""
    #local tld=""
    tld_black = {}
    tld_white = {}
    dns_whitelist = []

    domaincount = 0                                        #Reset per list domain count

    print('Processing Top Level Domain list')

    tld_blackfile = read_file(folders.tld_blacklist)
    tld_whitefile = read_file(folders.tld_whitelist)
    tld_csv = read_csv(folders.tld_csv)

    #Read tld's from tld_blacklist into tld_black dictionary
    for line in tld_blackfile:
        matches = Regex_TLDLine.search(line)
        if matches is not None:
            tld_black[matches.group(1)] = True

    #Read tld's from tld_whitelist into tld_white dictionary
    for line in tld_whitefile:
        matches = Regex_TLDLine.search(line)
        if matches is not None:
            tld_white[matches.group(1)] = True

    for row in tld_csv:
        reverse = row[0][::-1] + '.'
        if row[2] == '1':                                  #Risk 1 - High Risk
            if row[0] not in tld_white:                    #Is tld not in whitelist?
                blocktlddict[row[0]] = True                #Add high risk tld
                blocklist.append(tuple([reverse, row[0], row[1], 'bl_tld', ]))
                domaincount += 1
        else:
            if row[0] in tld_black:                        #Low risk, but in Black list
                blocktlddict[row[0]] = True                #Add low risk tld
                blocklist.append(tuple([reverse, row[0], row[1], 'bl_tld', ]))
                domaincount += 1

    print('\tAdded %d Top Level Domains' % domaincount)


    #Check for white listed domains that are blocked by tld
    for line in whitedict:
        matches = Regex_Domain.search(line)                #Only need the tld
        if matches.group(2) in blocktlddict:               #Is tld in blocktlddict?
            dns_whitelist.append(add_whitelist(line))

    if len(dns_whitelist) > 0:                             #Any domains in whitelist?
        print('\t%d domains added to whitelist in order avoid block from TLD' % len(dns_whitelist))
        save_list(dns_whitelist, folders.dnslists + 'whitelist.list')
    else:
        print('\tNo domains require whitelisting')
        delete_file(folders.dnslists + 'whitelist.list')
    whitedict.clear()                                      #whitedict no longer required
    print()


def process_whitelist():
    """
    Load items from whitelist file into blockdomiandict array
        (A domain being in the blocklist will prevent it from being added later)
    """
    global blockdomiandict, whitedict
    whitedict_len = 0

    sqldata = []
    splitline = []

    print('Processing whitelist:')
    print('\tLoading whitelist %s' % folders.whitelist)

    filelines = read_file(folders.whitelist)               #Load White list
    if filelines == None:
        print('\tNothing in whitelist')
        delete_file(folders.dnslists + 'whitelist.list')
        return

    for line in filelines:                                 #Process each line
        splitline = line.split('#', 1)
        if splitline[0] == '\n' or splitline[0] == '':     #Ignore Comment line or Blank
            continue

        blockdomiandict[splitline[0][:-1]] = True
        whitedict[splitline[0][:-1]] = True

        if len(splitline) > 1:                             #Line has a comment
            sqldata.append(tuple(['whitelist', splitline[0][:-1], True, splitline[1][:-1]]))
        else:                                              #No comment, leave it blank
            sqldata.append(tuple(['whitelist', splitline[0][:-1], True, '']))

    #Count number of domains white listed
    whitedict_len = len(whitedict)
    if whitedict_len > 0:
        print('\tNumber of domains in whitelist: %d' % whitedict_len)
        dbwrapper.blocklist_insertdata(sqldata)
    else:
        print('\tNothing in whitelist')
        delete_file(folders.dnslists + 'whitelist.list')
    print()


def check_file_age(filename):
    """
    Has FORCE been set?
    Does file exist?
    Check last modified time is within MAX_AGE (2 days)

    Parameters:
        filename (str): File
    Returns:
        True update list
        False list within MAX_AGE
    """
    print('\tChecking age of %s' % filename)
    if FORCE:
        print('\tForced update')
        return True

    if not os.path.isfile(filename):
        print('\tFile missing')
        return True

    if CURRENT_TIME > (os.path.getmtime(filename) + MAX_AGE):
        print('\tFile older than 2 days')
        return True

    print('\tFile in date, not downloading')
    return False


def download_list(url, listname, destination):
    """
    Download file
    Request file is unzipped (if necessary)

    Parameters:
        url (str): URL
        listname (str): List name
        destination (str): File destination
    Returns:
        True success
        False failed download
    """
    extension = ''
    outputfile = ''


    #Prepare for writing downloaded file to temp folder
    if url.endswith('zip'):                                #Check file extension
        extension = 'zip'
        outputfile = '%s%s.zip' % (folders.tempdir, listname)
    else:                                                  #Other - Assume txt for output
        extension = 'txt'
        outputfile = destination

    if not download_file(url, outputfile):
        return False

    if extension == 'zip':                                 #Extract zip file?
        extract_list(outputfile, destination)

    return True


def action_lists():
    """
    Go through config and process each enabled list
    1. Skip disabled lists
    2. Check if list is downloaded or locally stored
    3. For downloaded lists
    3a. Check file age
    3b. Download new copy if out of date
    4. Read file into filelines list
    5. Process list based on type
    """
    blname = ''                                            #Block list name (shortened)
    blenabled = False
    blurl = ''                                             #Block list URL
    bltype = ''                                            #Block list type
    blfilename = ''                                        #Block list file name

    for bl in blocklistconf.items():
        blname = bl[0]
        blenabled = bl[1][0]
        blurl = bl[1][1]
        bltype = bl[1][2]

        if not blenabled:                                  #Skip disabled blocklist
            continue

        print('Processing %s:' % blname)

        #Is this a downloadable file or locally stored?
        if blurl.startswith('http') or blurl.startswith('ftp'):
            blfilename = folders.tempdir + blname + '.txt'    #Download to temp folder
            if check_file_age(blfilename):                 #Does file need freshening?
                download_list(blurl, blname, blfilename)
        else:                                              #Local file
            blfilename = blurl;                            #URL is actually the filename

        if bltype == TYPE_SPECIAL:
            if blname == 'bl_tld':
                process_tldlist()
                continue

        filelines = read_file(blfilename)                  #Read temp file

        if not filelines:                                  #Anything read from file?
            print('\tData missing unable to process %s' % blname)
            print()
            continue

        if bltype == TYPE_PLAIN:
            process_plainlist(filelines, blname)
        elif bltype == TYPE_EASYLIST:
            process_easylist(filelines, blname)
        elif bltype == TYPE_UNIXLIST:
            process_unixlist(filelines, blname)

        print('\tFinished processing %s' % blname)
        print()


def action_customlists():
    """
    Go through config and process each enabled list
    1. Skip disabled lists
    2. Check if list is downloaded or locally stored
    3. For downloaded lists
    3a. Check file age
    3b. Download new copy if out of date
    4. Read file into filelines list
    5. Process list based on type
    """
    blname = ''
    blurl = ''                                             #Block list URL
    blfilename = ''                                        #Block list file name
    i = 0                                                  #Loop position (for naming)

    customurllist = []

    print('Processing Custom Blocklists:')

    if config['bl_custom'] == '':
        print('\tNo custom blocklists set')
        print()
        return

    customurllist = config['bl_custom'].split(',')         #Explode comma seperated vals

    for blurl in customurllist:
        i += 1
        blname = 'bl_custom%d' % i                         #Make up a name
        print('Processing %s - %s' % (blname, blurl))

        #Is this a downloadable file or locally stored?
        if blurl.startswith('http') or blurl.startswith('ftp'):
            #Download to temp folder with loop position in file name
            blfilename = '%s%s.txt' % (folders.tempdir, blname)
            if check_file_age(blfilename):                 #Does file need freshening?
                download_list(blurl, blname, blfilename)
        else:                                              #Local file
            blfilename = blurl;

        filelines = read_file(blfilename)                  #Read temp file

        if not filelines:                                  #Anything read from file?
            print('\tData missing unable to process %s' % blname)
            print()
            continue

        process_customlist(filelines, blname)

        print('\tFinished processing %s' % blname)
        print()


def dedup_lists():
    """
    Final sort and then save list to file
    1. Sort the blocklist by the reversed domain (blocklist[x][0])
    2. Check if each item matches the beginning of the previous item
        (i.e. a subdomain of a blocked domain)
    3. Remove matched items from the list
    4. Add unique items into sqldata and blacklist
    5. Save blacklist to file
    6. Insert SQL data
    """
    global blocklist, dedupcount

    dns_blacklist = []
    sqldata = []
    prev = '\0'                                            #Previous has to be something (e.g. a null byte)

    dedupcount = 0

    print()
    print('Sorting and Deduplicating blocklist')

    blocklist.sort(key=lambda x: x[0])                     #Sort list on col0 "reversed"

    for item in blocklist:
        if item[0].startswith(prev):
            #print('Removing:', item)
            #blocklist.remove(item)
            dedupcount += 1
        else:
            #blocklist.append(tuple([reverse, subdomain, comment, source]))
            dns_blacklist.append(add_blacklist(item[1]))
            sqldata.append(tuple([item[3], item[1], True, item[2]]))
            prev = item[0]

    print('Further deduplicated %d domains' % dedupcount)
    print('Final number of domains in blocklist: %d' % len(dns_blacklist))

    save_list(dns_blacklist, folders.dnslists + 'notrack.list')
    dbwrapper.blocklist_insertdata(sqldata)


def generate_blacklist():
    """
    Check to see if black list exists in NoTrack config folder
    If it doesn't then generate some example commented out domains to block
    """
    tmp = []                                               #List to build contents of file

    if os.path.isfile(folders.blacklist):                  #Check if black list exists
        return False

    print('Creating Black List')
    tmp.append('#Use this file to create your own custom block list\n')
    tmp.append('#Run notrack script (sudo notrack) after you make any changes to this file\n')
    tmp.append('#doubleclick.net\n')
    tmp.append('#googletagmanager.com\n')
    tmp.append('#googletagservices.com\n')
    tmp.append('#polling.bbc.co.uk #BBC Breaking News Popup\n')
    save_list(tmp, folders.blacklist)
    print()


def generate_whitelist():
    """
    Check to see if white list exists in NoTrack config folder
    If it doesn't then generate some example commented out domains to allow
    """
    tmp = []                                               #List to build contents of file

    if os.path.isfile(folders.whitelist):                  #Check if white list exists
        return False

    print('Creating White List')
    tmp.append('#Use this file to create your own custom block list\n')
    tmp.append('#Run notrack script (sudo notrack) after you make any changes to this file\n')
    tmp.append('#doubleclick.net\n')
    tmp.append('#googletagmanager.com\n')
    tmp.append('#googletagservices.com\n')
    save_list(tmp, folders.whitelist)
    print()


def show_version():
    """
    Show version number and exit
    """
    print('NoTrack Version %s' % VERSION)
    print()
    sys.exit(0)


def test():
    """
    Display config of block list choices
    """
    print('Block Lists enabled:')

    for bl in blocklistconf.items():
        if bl[1][0]:
            print(bl[0])

    print()
    if config['bl_custom'] == '':
        print('No additional custom block lists set')
    else:
        print('Additional custom block lists:')
        print(config['bl_custom'].replace(',', '\n'))

    sys.exit(0)

def notrack_checkupgrade():
    """
    Check for upgrades to NoTrack
    If autoupgrade is enabled then carry out upgrade
    """
    from ntrkupgrade import NoTrackUpgrade

    update_available = False

    if FORCE:                          #skip checking for upgrade if force mode is used
        return

    ntrkupgrade = NoTrackUpgrade(folders.tempdir, folders.sbindir, folders.wwwconfdir)
    update_available = ntrkupgrade.get_latestversion()

    if config['autoupgrade'] == '1' and update_available:
        ntrkupgrade.do_upgrade()


def notrack_pause(pausetime):
    """
    Check running as root
    Create ntrkpause class
    Setup pause blocking
    Check new status
        On error run notrack, set status to enabled
        On success restart DNS server, and change config['status']
    Wait
    Check new status
        On error run notrack, set status to enabled
        On success restart DNS server, and change config['status']

    Parameters:
        pausetime (int): Time to pause in minutes
    """
    check_root()
    ntrkpause = NoTrackPause(folders.tempdir, folders.main_blocklist)

    [newstatus, unpause_time] = ntrkpause.pause_blocking(config['status'], pausetime)

    if newstatus == STATUS_ERROR:                          #Something wrong with status
        notrack()                                          #Rerun notrack
        config['status'] = str(STATUS_ENABLED)             #Set status to enabled
        config['unpausetime'] = '0'                        #Reset pause time
        save_config()
        return

    config['status'] = str(newstatus)                      #Success, Update status
    config['unpausetime'] = str(unpause_time)              #Update unpausetime
    save_config()
    services.restart_dnsserver()                           #Restart DNS

    newstatus = ntrkpause.wait()

    if newstatus == STATUS_ERROR:                          #Something wrong with status
        notrack()                                          #Rerun notrack
        config['status'] = str(STATUS_ENABLED)             #Set status to enabled
        config['unpausetime'] = '0'                        #Reset pause time
        save_config()
        return

    config['status'] = str(newstatus)                      #Success, Update status
    config['unpausetime'] = '0'                            #Reset pause time
    save_config()
    services.restart_dnsserver()                           #Restart DNS


def notrack_play():
    """
    Check running as root
    Create ntrkpause class
    Enable blocking
    Check new status
        On error run notrack, set status to enabled
        On success restart DNS server, and change config['status']
    Save config file
    """
    check_root()
    ntrkpause = NoTrackPause(folders.tempdir, folders.main_blocklist)

    newstatus = ntrkpause.enable_blocking(config['status'])

    if newstatus == STATUS_ERROR:                          #Something wrong with status
        notrack()                                          #Rerun notrack
        config['status'] = str(STATUS_ENABLED)             #Set status to enabled
    else:
        services.restart_dnsserver()                       #Success, restart DNS
        config['status'] = str(newstatus)                  #Update status

    config['unpausetime'] = '0'                            #Reset pause time
    save_config()                                          #Write status change to config


def notrack_stop():
    """
    Check running as root
    Create ntrkpause class
    Disable blocking
    Check new status
        On error run notrack, set status to enabled
        On success restart DNS server, and change config['status']
    Save config file
    """
    check_root()
    ntrkpause = NoTrackPause(folders.tempdir, folders.main_blocklist)

    newstatus = ntrkpause.disable_blocking(config['status'])

    if newstatus == STATUS_ERROR:                          #Something wrong with status
        notrack()                                          #Rerun notrack
        config['status'] = str(STATUS_ENABLED)             #Set status to enabled
    else:
        services.restart_dnsserver()                       #Success, restart DNS
        config['status'] = str(newstatus)                  #Update status

    config['unpausetime'] = '0'                            #Reset pause time
    save_config()                                          #Write status change to config


def notrack_status():
    """
    Shows the current status from ntrkpause
    """
    ntrkpause = NoTrackPause(folders.tempdir, folders.main_blocklist)
    ntrkpause.get_detailedstatus(config['status'], config['unpausetime'])


def notrack(delay=0):
    """
    Main NoTrack function

    Parameters:
        delay (int): Optional delay start
    """
    if delay == 0:                                         #Any delay needed?
        print('Starting NoTrack')
    else:
        print('Starting NoTrack in delayed mode')
        time.sleep(delay)
        print('Delay finished')

    check_root()
    if is_running() > 0:                                   #Already running?
        sys.exit(8)

    dbwrapper.blocklist_createtable()                      #Create SQL Tables
    dbwrapper.blocklist_cleartable()                       #Clear SQL Tables

    generate_blacklist()
    generate_whitelist()

    process_whitelist()                                    #Need whitelist first
    action_lists()                                         #Action default lists
    action_customlists()                                   #Action users custom lists

    print('Finished processing all block lists')
    print('Total number of domains added: %d' % len(blocklist))
    print('Total number of domains deduplicated: %d' % totaldedupcount)
    dedup_lists()                                          #Dedup then insert domains

    services.restart_dnsserver()

    notrack_checkupgrade()


#Main----------------------------------------------------------------
parser = argparse.ArgumentParser(description = 'NoTrack')
parser.add_argument('-p', "--play", help='Start Blocking', action='store_true')
parser.add_argument('-s', "--stop", help='Stop Blocking', action='store_true')
parser.add_argument('--pause', help='Pause Blocking', type=int)
parser.add_argument('--status', help='Check Status', action='store_true')
parser.add_argument('--force', help='Force update block lists', action='store_true')
parser.add_argument('--wait', help='Delay start', action='store_true')
parser.add_argument('--search', help='Search block lists for a specified domain')
parser.add_argument('--test', help='Show current configuration', action='store_true')
parser.add_argument('-v', '--version', help='Get version number', action='store_true')

args = parser.parse_args()

if args.version:                                           #Showing version?
    show_version()

#Add any OS specific folder locations
folders = FolderList()
blocklistconf['bl_usersblacklist'] = [True, folders.blacklist, TYPE_PLAIN]
blocklistconf['bl_blacklist'][1] = folders.blacklist

services = Services()                                      #Declare service class
dbwrapper = DBWrapper()                                    #Declare MariaDB Wrapper
load_config()                                              #Load users config
host = Host(config['ipaddress'])                           #Declare host class

print('NoTrack version %s' % VERSION)
print('Hostname: %s' % host.name)
print('IP Address: %s' % host.ip)

#Setup the template strings for writing out to black/white list files
[dnsserver_blacklist, dnsserver_whitelist] = services.get_dnstemplatestr(host.name, host.ip)

#Process Arguments
if args.force:                                             #Force Download blocklists
    FORCE = True
if args.test:
    test()

print()

if args.pause:
    notrack_pause(args.pause)
elif args.play:
    notrack_play()
elif args.stop:
    notrack_stop()
elif args.status:
    notrack_status()
elif args.search:
    dbwrapper.blocklist_search(args.search)
elif args.wait:
    notrack(300)
else:
    notrack()

del dbwrapper
#TODO Check for updates
