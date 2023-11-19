#!/bin/python3
from pirc522 import RFID
import time
import signal
import sys
import struct
from datetime import datetime
import logging
import uuid
import requests
import socket

API_ENPOINT='https://37dff9c1-b164-4821-93d5-59684591f4f0.ma.bw-cloud-instance.org'


reader = RFID()
util = reader.util()


# Set up the logging output printed to the console
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Enable additional output when program is in debug mode
if logger.getEffectiveLevel() == logging.DEBUG:
	util.debug = True


# Get a unique device ID based on the MAC address of a network interface
# This ID never changes between device reboots or even reinstalls
# UUID based on RFC4122 were unsuitable since they may change between reboots
id = uuid.UUID(int=uuid.getnode())
station_ID = str(id)


# Try to register this reader with the server
# In case the server isn't reachable, continue in offline mode
# The server feature is a nice addon so it isn't too problematic if it fails
online = False
try:
	r = requests.post(f'{API_ENPOINT}/api/stations', timeout=5, json={
		'station_uuid': station_ID,
		'name': socket.gethostname()
	})
	online = True
except requests.exceptions.RequestException as e:
	logging.error(f'API Endpoint {API_ENPOINT} not reachable')


# The program should be launchable from the CLI
# Therefore it is criticial to gracefully shut down when SIGKILL event is sent (e.g. via CTRL+C)
# Catching a KeyboardInterrupt allows us to handle the event properly
try:
	logging.info('Starting poll loop')

	while True:
		# Wait for a tag to come into sending range
		logging.info('Waiting for tag\n')
		reader.wait_for_tag()

		# Request tag
		(error, data) = reader.request()
		if error:
			continue

		logging.info('Tag(s) detected')

		# Make sure we are only communicating with one tag
		(error, uid) = reader.anticoll()
		if error:
			continue
		
		# Print UID
		tag_ID = f'{uid[0]}-{uid[1]}-{uid[2]}-{uid[3]}'
		logging.info(tag_ID)

		# Send update to server
		if online:
			r = requests.post(f'{API_ENPOINT}/api/stations/{station_ID}/tags', timeout=5, json={
				'tag_uuid': tag_ID,
				'last_seen': 0,
				'previously_seen': 0
			})	

			logging.info(r.status_code)

		# Set tag as used in util. This will call RFID.select_tag(uid)
		util.set_tag(uid)

		# Save authorization info (key B) to util
		# This is needed later when performing operations on the tag
		util.auth(reader.auth_b, [0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])
		

		# Show the last seen information
		# The timestamp is stored in the first block of the first usable sector (2)
		timestamp_address = util.block_addr(1, 0)
		logging.debug(f'Timestamp block address: {timestamp_address}')

		# Perform an initial authentication for the sector  
		util.do_auth(timestamp_address)
		
		# Read the timestamp 
		error, data = reader.read(timestamp_address)
		if error: 
			logging.error('Last seen timestamp could not been read')
		else:
			# pirc522 returns data read as a 16-byte aligned list of bytes  (y???????!?)
			# To make the data usable, we need to convert them into a regular integer
			# Bytes are interpreted from little endian
			data = int.from_bytes(bytes(data), "little")

			# A unix timestamp isn't easy to read
			# To make our live easier we convert it into a string representation using a regular format
			# See https://docs.python.org/3/library/datetime.html#strftime-and-strptime-format-codes for more information
			timestamp_human_readable = datetime.utcfromtimestamp(data).strftime('%Y-%m-%d %H:%M:%S')
			logging.info(f'Tag last seen on {timestamp_human_readable}')

			# Update the last seen timestamp with the current time
			# Get a 4 byte unix timestamp
			now = int(time.time())
			logging.debug(datetime.utcfromtimestamp(now).strftime('%Y-%m-%d %H:%M:%S'))

			# pirc522 expects data to be written to also be in a 16-byte aligned list of bytes
			# To do so we have to convert the regular int timestamp into a byte object and then pad it accordingly
			# First convert the int into a byte object
			# Data stored on tags are stored in little endian (https://www.nxp.com/docs/en/data-sheet/MF1S50YYX_V1.pdf, p. 9)
			data = struct.pack("<i", now)

			# Then pad the data to 16 bytes by adding 12 bytes of zeros
			data = data + bytearray(12)

			# Write the data to the tag
			error = reader.write(timestamp_address, data)
			if error:
				logging.error('Write of timestamp not successful')


		# Read the previously seen counter and show it 
		# The counter is stored in the first block of the second usable sector (3)
		# Get address of sector 3 Block 1
		counter_address = util.block_addr(2, 0)
		logging.debug(f'Counter block address: {counter_address}')

		# Because we are using a new sector we have to authenticate again
		util.do_auth(counter_address)

		# Read the counter
		error, data = reader.read(counter_address)
		if error: 
			logging.error('Previously seen counter could not been read')
		else:
			# The counter is store in the first byte of the first block
			counter = data[0]

			if counter <= 0:
				logging.info('I\'ve never met this tag in my life')
			else:
				logging.info(f'Tag previously seen {counter} times. ')

			# Increment the counter and save on tag
			# Careful: Because the variable is actually only one byte, it will overflow after 255 writes
			# This could be fixed by incrementing the variables width
			data[0] = counter + 1

			error = reader.write(counter_address, data)
			if error:
				logging.error('Write of previously seen counter not successful')


		# After finishing all operations on the tag we can deauthenticate and remove all credentials from memory
		util.deauth()

		# Wait for 3 seconds before doing anything else
		# This isn't technically necessary, it's just to make the demonstration a little easier
		time.sleep(3)


except KeyboardInterrupt:
	reader.cleanup()		
