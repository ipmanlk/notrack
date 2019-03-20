#!/bin/bash
#Title : NoTrack Analytics
#Description : Analyse dns_logs for suspect lookups to malicious or unknown tracking sites
#Author : QuidsUp
#Date : 2019-03-17
#Usage : 


#######################################
# Constants
#######################################
readonly USER="ntrk"
readonly PASSWORD="ntrkpass"
readonly DBNAME="ntrkdb"


#######################################
# Global Variables
#######################################
declare -a results
declare -A blocklists


#######################################
# Error Exit
#
# Globals:
#   None
# Arguments:
#  $1. Error Message
#  $2. Exit Code
# Returns:
#   None
#
#######################################
function error_exit() {
  echo "Error: $1"
  echo "Aborting"
  exit "$2"
}


#######################################
# Create SQL Tables
#   Create SQL tables for analytics, in case it has been deleted
#
# Globals:
#   USER, PASSWORD, DBNAME
# Arguments:
#   None
# Returns:
#   None
#
#######################################
function create_sqltables() {
  mysql --user="$USER" --password="$PASSWORD" -D "$DBNAME" -e "CREATE TABLE IF NOT EXISTS analytics (id BIGINT UNSIGNED NOT NULL PRIMARY KEY AUTO_INCREMENT, log_time DATETIME, sys TINYTEXT, dns_request TINYTEXT, dns_result CHAR(1), issue TINYTEXT, ack BOOLEAN);"
}


#######################################
# Delete Blocklist table
#   1. Delete all rows in Table
#   2. Reset Counter
#
# Globals:
#   USER, PASSWORD, DBNAME
# Arguments:
#   None
# Returns:
#   None
#
#######################################
function delete_table() {
  echo "Clearing Blocklist Table"

  #echo "DELETE FROM blocklist;" | mysql --user="$USER" --password="$PASSWORD" -D "$DBNAME"
  #echo "ALTER TABLE blocklist AUTO_INCREMENT = 1;" | mysql --user="$USER" --password="$PASSWORD" -D "$DBNAME"
}


#######################################
# Get Available Block Lists
#   1. Find the distinct blocklists the user has selected in blocklist table
#   2. Add them to associative array blocklists
#
# Globals:
#   DBNAME, PASSWORD, USER, blocklists
# Arguments:
#   1. Blocklist Code
# Returns:
#   None
#
#######################################
function get_blocklists() {
  local -a templist
  local str=""
  
  echo "Checking which blocklists are in use"
  
  mapfile templist < <(mysql --user="$USER" --password="$PASSWORD" -D "$DBNAME" --batch -e "SELECT DISTINCT bl_source FROM blocklist;")
  
  for str in "${templist[@]}"; do
    str="${str//[[:space:]]/}"                             #Remove spaces and tabs
    blocklists[$str]=true                                  #Add key to blocklists
  done
}


#######################################
# Check Malware
#   
#
# Globals:
#   DBNAME, PASSWORD, USER
# Arguments:
#   1. Blocklist Code
# Returns:
#   None
#
#######################################
function check_malware() {
  local bl="$1"                                            #Blocklist
  results=()                                               #Clear results array
  
  echo "Searching for domains from $bl"
  mapfile results < <(mysql --user="$USER" --password="$PASSWORD" -D "$DBNAME" --batch -e "SELECT * FROM dnslog WHERE log_time >= DATE_SUB(NOW(), INTERVAL 2 HOUR) AND dns_request IN (SELECT site FROM blocklist WHERE bl_source = '$bl');")
  
  if [ ${#results[@]} -gt 1 ]; then
    review_results "Malware-$bl"
  fi
 
}



#######################################
# Review Results
#   Split each tabbed seperated array item of results and then
#    send those arrays (minus id) to insert_data
#
# Globals:
#   results
# Arguments:
#   1. What the result is from
# Returns:
#   None
#
#######################################
function review_results() {
  local issue="$1"
  local -i results_size=0
  local -i i=1
  
  #Group 1: id
  #Group 2: log_time
  #Group 3: sys
  #Group 4: dns_request
  #Group 5: dns_result
  
  results_size=${#results[@]}
  echo "Found $results_size domains"
  
  while [ $i -lt "$results_size" ]
  do    
    if [[ ${results[$i]} =~ ^([0-9]+)[[:blank:]]([0-9\-]+[[:blank:]][0-9:]+)[[:blank:]]([^[:blank:]]+)[[:blank:]]([^[:blank:]]+)[[:blank:]]([ABCL]) ]]; then
      insert_data "${BASH_REMATCH[2]}" "${BASH_REMATCH[3]}" "${BASH_REMATCH[4]}" "${BASH_REMATCH[5]}" "$issue"
    fi
    ((i++))
  done  
  
  echo
}


#######################################
# Insert Data into SQL Table
#
# Globals:
#   DBNAME, PASSWORD, USER
# Arguments:
#   $1. log_time
#   $2. system
#   $3. dns_request
#   $4. dns_result
#   $5. issue
# Returns:
#   None
#
#######################################

function insert_data() {
#echo "$1,$2,$3,$4,$5"                                     #Uncomment for debugging
 
mysql --user="$USER" --password="$PASSWORD" -D "$DBNAME" << EOF
INSERT INTO anlytics (id,log_time,sys,dns_request,dns_result,issue,ack) VALUES (NULL,'$1','$2','$3','$4','$5',FALSE);
EOF

#TODO error checking with #?
}

echo "NoTrack Analytics"

create_sqltables

get_blocklists
[ -n "${blocklists['bl_notrack_malware']}" ] && check_malware "bl_notrack_malware"
[ -n "${blocklists['bl_hexxium']}" ] && check_malware "bl_hexxium"
[ -n "${blocklists['bl_cedia']}" ] && check_malware "bl_cedia"
[ -n "${blocklists['bl_cedia_immortal']}" ] && check_malware "bl_cedia_immortal"
[ -n "${blocklists['bl_malwaredomainlist']}" ] && check_malware "bl_malwaredomainlist"
[ -n "${blocklists['bl_malwaredomains']}" ] && check_malware "bl_malwaredomains"
[ -n "${blocklists['bl_swissransom']}" ] && check_malware "bl_swissransom"
[ -n "${blocklists['bl_swisszeus']}" ] && check_malware "bl_swisszeus"
