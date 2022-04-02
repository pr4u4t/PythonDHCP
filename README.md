Python DHCP Server
------------------

This is a purely Python DHCP server that does not require any additional libraries or installs other that Python 3.

This DHCP server program will assign IP addresses ten seconds after it received packets from clients. So it can be used in networks that already have a dhcp server running.

First argument is of program is testet for being configuration file fg.
./dhcp.py dhcp.conf
if that file does not exists arguments are read from command line, strings must be so called double escaped "'string'"
dhcp.py -broadcast_address "[ '255.255.255.255' ]" -domain_name_server "'192.168.0.1'"

see dhcp.conf for more details

TEST:
on Server:
ifconfig ETH_CARD_NAME 192.168.137.0 up
./dhcp.py dhcp.conf

On client:
dhcpcd or dhclient ETH_CARD_NAME

ENSURE THAT CARDS HAVE CONNECTIVITY BEFORE TESTING

USE netcat or telnet to connect server command line:
nc 127.0.0.1 6868

type `help` to get list of commands


