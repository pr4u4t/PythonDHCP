#!/usr/bin/env python3

from os.path import exists
import re
from ttldict import  TTLOrderedDict
import socketserver
import time
import threading
import struct
import queue
import collections
import traceback
import random
import socket
import heapq    
import sys

from listener import *

class TransactionDelayWorker(object):
    """Class used to delay response to DHCP client
    """
    def __init__(self):
        """class constructor internally using priority queue where priority is time
        """
        self.closed = False
        self.queue = PriorityQueue()
        self.thread = threading.Thread(target = self._delay_response_thread)
        self.thread.start()

    def _delay_response_thread(self):
        """thread worker
        """
        while not self.closed:
            if self.closed:
                break
            if self.queue.qsize() > 0:
                p = self.queue.get()
                t, func, args, kw = p
                now = time.time()
                if now < t:
                    time.sleep(0.01)
                    self.queue.put(p)
                else:
                    func(*args, **kw)


    def do_after(self, seconds, func, args = (), kw = {}):
        """Add to queue function which should be called after certain time
        specified by seconds, args, kw are arguments
        """
        self.queue.put((time.time() + seconds, func, args, kw))

    def close(self):
        """Method used to stop worker
        """
        self.closed = True

def get_host_ip_addresses():
    """Get IP address of current host.
    """
    return gethostbyname_ex(gethostname())[2]

class PriorityQueue(object):
    """This class contains Heapq for more information:
    https://docs.python.org/3/library/heapq.html
    """
    def __init__(self):
        self._queue = []
        self._index = 0

    def put(self, item):
        heapq.heappush(self._queue, (self._index, item))
        self._index += 1

    def get(self):
        return heapq.heappop(self._queue)[-1]

    def qsize(self):
        return len(self._queue)

class WriteBootProtocolPacket(object):
    """DHCP protocol datagram serializer
    This class serializes UDP DHCP packet, instance is constructed using global 
    configuration from which dhcp options are copied
    """
    message_type = 2 # 1 for client -> server 2 for server -> client
    hardware_type = 1
    hardware_address_length = 6
    hops = 0

    transaction_id = None

    seconds_elapsed = 0
    bootp_flags = 0 # unicast

    client_ip_address = '0.0.0.0'
    your_ip_address = '0.0.0.0'
    next_server_ip_address = '0.0.0.0'
    relay_agent_ip_address = '0.0.0.0'

    client_mac_address = None
    magic_cookie = '99.130.83.99'

    parameter_order = []
    
    def __init__(self, configuration):
        """Create new packet instance and search for options set in configuration 
        and copy them tgo packet
        """
        for i in range(256):
            names = ['option_{}'.format(i)]
            if i < len(options) and hasattr(configuration, options[i][0]):
                names.append(options[i][0])
            for name in names:
                if hasattr(configuration, name):
                    setattr(self, name, getattr(configuration, name))

    def to_bytes(self):
        """Serialize UDP DHCP response packet to bytes
        """
        result = bytearray(236)
        
        result[0] = self.message_type
        result[1] = self.hardware_type
        result[2] = self.hardware_address_length
        result[3] = self.hops

        result[4:8] = struct.pack('>I', self.transaction_id)

        result[ 8:10] = shortpack(self.seconds_elapsed)
        result[10:12] = shortpack(self.bootp_flags)

        result[12:16] = inet_aton(self.client_ip_address)
        result[16:20] = inet_aton(self.your_ip_address)
        result[20:24] = inet_aton(self.next_server_ip_address)
        result[24:28] = inet_aton(self.relay_agent_ip_address)

        result[28:28 + self.hardware_address_length] = macpack(self.client_mac_address)
        
        result += inet_aton(self.magic_cookie)

        for option in self.options:
            value = self.get_option(option)
            #print(option, value)
            if value is None:
                continue
            result += bytes([option, len(value)]) + value
        result += bytes([255])
        return bytes(result)

    def get_option(self, option):
        """Get DHCP UDP response packet option value
        """
        if option < len(options) and hasattr(self, options[option][0]):
            value = getattr(self, options[option][0])
        elif hasattr(self, 'option_{}'.format(option)):
            value = getattr(self, 'option_{}'.format(option))
        else:
            return None
        function = options[option][2]
        if function and value is not None:
            value = function(value)
        return value
    
    @property
    def options(self):
        """Get DHCP UDP response packet option value
        """
        done = list()
        # fulfill wishes
        for option in self.parameter_order:
            if option < len(options) and hasattr(self, options[option][0]) or hasattr(self, 'option_{}'.format(option)):
                # this may break with the specification because we must try to fulfill the wishes
                if option not in done:
                    done.append(option)
        # add my stuff
        for option, o in enumerate(options):
            if o[0] and hasattr(self, o[0]):
                if option not in done:
                    done.append(option)
        for option in range(256):
            if hasattr(self, 'option_{}'.format(option)):
                if option not in done:
                    done.append(option)
        return done

    def __str__(self):
        """Serialize UDP DHCP response packet to bytes
        """
        return str(ReadBootProtocolPacket(self.to_bytes()))

class DHCPTransaction(object):
    """Class representing DHCP Transaction
    """
    def __init__(self, server):
        """Contructor of new transaction
        """
        self.server = server
        self.configuration = server.configuration
        self.packets = []
        self.done_time = time.time() + self.configuration.length_of_transaction
        self.done = False
        self.do_after = self.server.delay_worker.do_after
        self.debug = debug
        
    def is_done(self):
        """Check if transaction is done 
        """
        return self.done or self.done_time < time.time()

    def close(self):
        """Close transaction
        """
        self.done = True

    def receive(self, packet):
        """Receive DHCP UDP packet check it's type and call a proper callback
        """
        # packet from client <-> packet.message_type == 1
        if packet.message_type == 1 and packet.dhcp_message_type == 'DHCPDISCOVER':
            self.do_after(self.configuration.dhcp_offer_after_seconds,
                          self.received_dhcp_discover, (packet,), )
        elif packet.message_type == 1 and packet.dhcp_message_type == 'DHCPREQUEST':
            self.do_after(self.configuration.dhcp_acknowledge_after_seconds,
                          self.received_dhcp_request, (packet,), )
        elif packet.message_type == 1 and packet.dhcp_message_type == 'DHCPINFORM':
            self.received_dhcp_inform(packet)
        else:
            return False
        return True

    def received_dhcp_discover(self, discovery):
        """Method used to handle DHCP Discover packet
        """
        if self.is_done(): return
        self.configuration.debug('discover:\n {}'.format(str(discovery).replace('\n', '\n\t')))
        self.send_offer(discovery)

    def send_offer(self, discovery):
        """Method used to send DHCP offer packet
        """
        # https://tools.ietf.org/html/rfc2131
        offer = WriteBootProtocolPacket(self.configuration)
        offer.parameter_order = discovery.parameter_request_list
        mac = discovery.client_mac_address
        ip = offer.your_ip_address = self.server.get_ip_address(discovery)
        # offer.client_ip_address = 
        offer.transaction_id = discovery.transaction_id
        # offer.next_server_ip_address =
        offer.relay_agent_ip_address = discovery.relay_agent_ip_address
        offer.client_mac_address = mac
        offer.client_ip_address = discovery.client_ip_address or '0.0.0.0'
        offer.bootp_flags = discovery.bootp_flags
        offer.dhcp_message_type = 'DHCPOFFER'
        offer.client_identifier = mac
        self.configuration.debug('offer:\n {}'.format(str(offer).replace('\n', '\n\t')))
        self.server.broadcast(offer)
    
    def received_dhcp_request(self, request):
        """Method used to handle DHCP Request packet
        """
        if self.is_done(): return 
        self.configuration.debug('request:\n {}'.format(str(request).replace('\n', '\n\t')))
        self.server.client_has_chosen(request)
        self.acknowledge(request)
        self.close()

    def acknowledge(self, request):
        """Method used to handle DHCP Acknowledge packet
        """
        ack = WriteBootProtocolPacket(self.configuration)
        ack.parameter_order = request.parameter_request_list
        ack.transaction_id = request.transaction_id
        # ack.next_server_ip_address =
        ack.bootp_flags = request.bootp_flags
        ack.relay_agent_ip_address = request.relay_agent_ip_address
        mac = request.client_mac_address
        ack.client_mac_address = mac
        requested_ip_address = request.requested_ip_address
        ack.client_ip_address = request.client_ip_address or '0.0.0.0'
        ack.your_ip_address = self.server.get_ip_address(request)
        ack.dhcp_message_type = 'DHCPACK'
        self.configuration.debug('acknowledge:\n {}'.format(str(ack).replace('\n', '\n\t')))
        self.server.broadcast(ack)

    def received_dhcp_inform(self, inform):
        """Method used to handle DHCP Inform packet
        """
        self.configuration.debug('inform:\n {}'.format(str(inform).replace('\n', '\n\t')))
        self.close()
        self.server.client_has_chosen(inform)

class DHCPServerConfiguration(object):
    """Class to load DHCP server configuration from file or command line
    """
    dhcp_offer_after_seconds = 10
    dhcp_acknowledge_after_seconds = 10
    length_of_transaction = 40

    network = '192.168.173.0'
    broadcast_address = '255.255.255.255'
    subnet_mask = '255.255.255.0'
    router = None # list of ips
    # 1 day is 86400
    ip_address_lease_time = 300 # seconds
    domain_name_server = None # list of ips

    host_file = 'hosts.csv'

    debug = lambda *args, **kw: None

    def load(self, file):
        """Load configuration from file using exec to parse file as object dictionary
        or get ALL command line arguments and change them using regexp to file layout
        and treat as file
        """
        if(len(file) > 0 and exists(file)):
            with open(file) as f:
                exec(f.read(), self.__dict__)
        else:
            args = ' '.join(sys.argv[1:])
            args = re.sub(' -', "\r\n", args)
            args = re.sub('^-', '', args)
            args = re.sub('^([a-z_]+)([ ]+)(.+)$', r"\1=\3", args, flags=re.MULTILINE)
            exec(args, self.__dict__)

    def adjust_if_this_computer_is_a_router(self):
        """Automatically adjust some DHCP configuration parameters if this computer is router
        """
        ip_addresses = get_host_ip_addresses()
        for ip in reversed(ip_addresses):
            if ip.split('.')[-1] == '1':
                self.router = [ip]
                self.domain_name_server = [ip]
                self.network = '.'.join(ip.split('.')[:-1] + ['0'])
                self.broadcast_address = '.'.join(ip.split('.')[:-1] + ['255'])
                #self.ip_forwarding_enabled = True
                #self.non_local_source_routing_enabled = True
                #self.perform_mask_discovery = True

    def all_ip_addresses(self):
        ips = ip_addresses(self.network, self.subnet_mask)
        for i in range(5):
            next(ips)
        return ips

    def network_filter(self):
        return NETWORK(self.network, self.subnet_mask)

def ip_addresses(network, subnet_mask):
    import socket, struct
    subnet_mask = struct.unpack('>I', socket.inet_aton(subnet_mask))[0]
    network = struct.unpack('>I', socket.inet_aton(network))[0]
    network = network & subnet_mask
    start = network + 1
    end = (network | (~subnet_mask & 0xffffffff))
    return (socket.inet_ntoa(struct.pack('>I', i)) for i in range(start, end))

class ALL(object):
    """Comparator class
    """
    def __eq__(self, other):
        return True
    def __repr__(self):
        return self.__class__.__name__
    
ALL = ALL()

class GREATER(object):
    """Comparator class
    """
    def __init__(self, value):
        self.value = value
    def __eq__(self, other):
        return type(self.value)(other) > self.value

class NETWORK(object):
    """Comparator class to check if address within same network
    """
    def __init__(self, network, subnet_mask):
        self.subnet_mask = struct.unpack('>I', inet_aton(subnet_mask))[0]
        self.network = struct.unpack('>I', inet_aton(network))[0]
    def __eq__(self, other):
        ip = struct.unpack('>I', inet_aton(other))[0]
        return ip & self.subnet_mask == self.network and \
               ip - self.network and \
               ip - self.network != ~self.subnet_mask & 0xffffffff
        
class CASEINSENSITIVE(object):
    """Comparator class
    """
    def __init__(self, s):
        self.s = s.lower()
    def __eq__(self, other):
        return self.s == other.lower()

class CSVDatabase(object):
    """Class handling CSV file database to keep host definitions
    """
    delimiter = ';'

    def __init__(self, file_name):
        """Construct new CSV database with storage in file_name
        """
        self.file_name = file_name
        self.file('a').close() # create file

    def file(self, mode = 'r'):
        """Open CSV file with selected mode
        """
        return open(self.file_name, mode)

    def get(self, pattern):
        """Get CSV entry representing host(MAC) and lease(IP)
        """
        pattern = list(pattern)
        return [line for line in self.all() if pattern == line]

    def add(self, line):
        """Add host entry to CSV file
        """
        with self.file('a') as f:
            f.write(self.delimiter.join(line) + '\n')

    def delete(self, pattern):
        """Delete host entry from CSV file
        """
        lines = self.all()
        lines_to_delete = self.get(pattern)
        self.file('w').close() # empty file
        for line in lines:
            if line not in lines_to_delete:
                self.add(line)

    def all(self):
        """Get all entries from CSV file
        """
        with self.file() as f:
            return [list(line.strip().split(self.delimiter)) for line in f]

class Host(object):
    """Class representing host with MAC address, IP, hostname if available and last used timestamp
    """
    def __init__(self, mac, ip, hostname, last_used):
        self.mac = mac.upper()
        self.ip = ip
        self.hostname = hostname
        self.last_used = int(last_used)

    @classmethod
    def from_tuple(cls, line):
        mac, ip, hostname, last_used = line
        last_used = int(last_used)
        return cls(mac, ip, hostname, last_used)

    @classmethod
    def from_packet(cls, packet):
        return cls(packet.client_mac_address,
                   packet.requested_ip_address or packet.client_ip_address,
                   packet.host_name or '',
                   int(time.time()))

    @staticmethod
    def get_pattern(mac = ALL, ip = ALL, hostname = ALL, last_used = ALL):
        return [mac, ip, hostname, last_used]

    def to_tuple(self):
        """Convert host to tuple
        """
        return [self.mac, self.ip, self.hostname, str(int(self.last_used))]

    def to_pattern(self):
        """Convert host to pattern
        """
        return self.get_pattern(ip = self.ip, mac = self.mac)

    def __hash__(self):
        return hash(self.key)

    def __eq__(self, other):
        return self.to_tuple() == other.to_tuple()

    def has_valid_ip(self):
        """Check if host has valid IP address
        """
        return self.ip and self.ip != '0.0.0.0'
        
class HostDatabase(object):
    def __init__(self, file_name):
        self.db = CSVDatabase(file_name)

    def get(self, **kw):
        pattern = Host.get_pattern(**kw)
        return list(map(Host.from_tuple, self.db.get(pattern)))

    def add(self, host):
        self.db.add(host.to_tuple())

    def delete(self, host = None, **kw):
        if host is None:
            pattern = Host.get_pattern(**kw)
        else:
            pattern = host.to_pattern()
        self.db.delete(pattern)

    def all(self):
        return list(map(Host.from_tuple, self.db.all()))

    def replace(self, host):
        self.delete(host)
        self.add(host)
        
def sorted_hosts(hosts):
    hosts = list(hosts)
    hosts.sort(key = lambda host: (host.hostname.lower(), host.mac.lower(), host.ip.lower()))
    return hosts

class DHCPServer(object):
    """Main DHCP server class that is handling incoming packets and sending responses
    using all other utility classes
    """
    def __init__(self, configuration = None):
        if configuration == None:
            configuration = DHCPServerConfiguration()
            
        self.configuration = configuration
        #OPEN UDP SOCKET FOR HANDLING INCOMING DHCP PACKETS and SENDING RESPONSES
        self.socket = socket(type = SOCK_DGRAM)
        self.socket.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
        self.socket.bind(('', 67))
        self.delay_worker = TransactionDelayWorker()
        self.closed = False
        self.transactions = collections.defaultdict(lambda: DHCPTransaction(self)) # id: transaction
        self.hosts = HostDatabase(self.configuration.host_file)
        self.time_started = time.time()

    def close(self):
        self.socket.close()
        self.closed = True
        self.delay_worker.close()
        for transaction in list(self.transactions.values()):
            transaction.close()

    def update(self, timeout = 0):
        try:
            reads = select.select([self.socket], [], [], timeout)[0]
        except ValueError:
            # ValueError: file descriptor cannot be a negative integer (-1)
            return
        for socket in reads:
            try:
                packet = ReadBootProtocolPacket(*socket.recvfrom(4096))
            except OSError:
                # OSError: [WinError 10038] An operation was attempted on something that is not a socket
                pass
            else:
                self.received(packet)
        for transaction_id, transaction in list(self.transactions.items()):
            if transaction.is_done():
                transaction.close()
                self.transactions.pop(transaction_id)

    def received(self, packet):
        if not self.transactions[packet.transaction_id].receive(packet):
            self.configuration.debug('received:\n {}'.format(str(packet).replace('\n', '\n\t')))
            
    def client_has_chosen(self, packet):
        self.configuration.debug('client_has_chosen:\n {}'.format(str(packet).replace('\n', '\n\t')))
        host = Host.from_packet(packet)
        if not host.has_valid_ip():
            return
        self.hosts.replace(host)

    def is_valid_client_address(self, address):
        if address is None:
            return False
        a = address.split('.')
        s = self.configuration.subnet_mask.split('.')
        n = self.configuration.network.split('.')
        return all(s[i] == '0' or a[i] == n[i] for i in range(4))

    def get_ip_address(self, packet):
        mac_address = packet.client_mac_address
        requested_ip_address = packet.requested_ip_address
        known_hosts = self.hosts.get(mac = CASEINSENSITIVE(mac_address))
        ip = None
        if known_hosts:
            # 1. choose known ip address
            for host in known_hosts:
                if self.is_valid_client_address(host.ip):
                    ip = host.ip
            self.configuration.debug('known ip:{}'.format(ip))
        if ip is None and self.is_valid_client_address(requested_ip_address):
            # 2. choose valid requested ip address
            ip = requested_ip_address
            self.configuration.debug('valid ip:{}'.format(ip))
        if ip is None:
            # 3. choose new, free ip address
            chosen = False
            network_hosts = self.hosts.get(ip = self.configuration.network_filter())
            for ip in self.configuration.all_ip_addresses():
                if not any(host.ip == ip for host in network_hosts):
                    chosen = True
                    break
            if not chosen:
                # 4. reuse old valid ip address
                network_hosts.sort(key = lambda host: host.last_used)
                ip = network_hosts[0].ip
                assert self.is_valid_client_address(ip)
            self.configuration.debug('new ip:'.format(ip))
        if not any([host.ip == ip for host in known_hosts]):
            self.configuration.debug('add {} {}'.format(mac_address, ip, packet.host_name))
            self.hosts.replace(Host(mac_address, ip, packet.host_name or '', time.time()))
        return ip

    @property
    def server_identifiers(self):
        return get_host_ip_addresses()

    def broadcast(self, packet):
        self.configuration.debug('broadcasting:\n {}'.format(str(packet).replace('\n', '\n\t')))
        for addr in self.server_identifiers:
            broadcast_socket = socket(type = SOCK_DGRAM)
            broadcast_socket.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
            broadcast_socket.setsockopt(SOL_SOCKET, SO_BROADCAST, 1)
            packet.server_identifier = addr
            broadcast_socket.bind((addr, 67))
            try:
                data = packet.to_bytes()
                broadcast_socket.sendto(data, ('255.255.255.255', 68))
                broadcast_socket.sendto(data, (addr, 68))
            finally:
                broadcast_socket.close()

    def run(self):
        while not self.closed:
            try:
                self.update(1)
            except KeyboardInterrupt:
                break
            except:
                traceback.print_exc()

    def run_in_thread(self):
        thread = threading.Thread(target = self.run)
        thread.start()
        return thread

    def debug_clients(self):
        for line in self.ips.all():
            line = '\t'.join(line)
            if line:
                self.configuration.debug(line)

    def get_all_hosts(self):
        return sorted_hosts(self.hosts.get())

    def get_current_hosts(self):
        return sorted_hosts(self.hosts.get(last_used = GREATER(self.time_started)))

class ThreadedTCPRequestHandler(socketserver.StreamRequestHandler):
    """Control socket client connection handler
    """
    def handle(self):
        """Method used to handle client connection parsing commands and giving response to them
        """
        self.request.sendall(bytes("Welcome to micro python dhcp server", 'ascii'))
        try:
            while(True):
                self.request.sendall(bytes("\r\npydhcp ?> ", 'ascii'))
                data = self.rfile.readline().strip()
                if(data.decode() == "hosts"):
                    self.request.sendall(bytes("Active Hosts:\r\n{}".format("\r\n".join(self.server.hosts.all())),'ascii'))
                elif(data.decode() == "events"):
                    self.request.sendall(bytes("Events last 24h:\r\n{}".format("\r\n".join(self.server.events.items())),'ascii'))
                elif(data.decode() == "configuration"):
                    self.request.sendall(bytes("Current configuration\r\n", 'ascii'))
                    for value in options:
                        if(hasattr(self.server.configuration,value[0])):
                            self.request.sendall(bytes("{}: {}\r\n".format(value[0],getattr(self.server.configuration,value[0])),'ascii'))
                elif(data.decode() == "help"):
                    self.request.sendall(bytes("hosts\t\tdisplay host database\r\n",'ascii'))
                    self.request.sendall(bytes("events\t\tdisplay DHCP event log\r\n",'ascii'))
                    self.request.sendall(bytes("configuration\tdisplay current server configuration\r\n",'ascii'))
                    self.request.sendall(bytes("help\t\tthis command\r\n",'ascii'))
                    self.request.sendall(bytes("quit\t\tdisconnect from current session\r\n",'ascii'))
                elif(data.decode() == "quit"):
                    self.request.sendall(bytes("bye\r\n", 'ascii'))
                    break
                else:
                    self.request.sendall(bytes("unknown command: {}".format(data.decode('ascii')), 'ascii'))
        except Exception as e:
            pass

class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    """DHCP server control interface TCP server
    """
    def setEvents(self,data):
        """Set DHCP events dictionary reference
        """
        self.events = data

    def setHosts(self,data):
        """Set DHCP host database with active leases reference
        """
        self.hosts = data
        
    def setConfiguration(self,data):
        """Set DHCP UDP global configuration reference
        """
        self.configuration = data


if __name__ == "__main__":
    HOST, PORT = "localhost", 6868
    messages = TTLOrderedDict(default_ttl=86400) #keep messages for 24h
    
    def debug_msg(msg,type):
        if bool(type):
            type = 'debug'
        messages[time.time()] = { 'type': type, 'msg': msg }

    if(len(sys.argv) == 1):
        print('configuration file or command line options must be passed')
        sys.exit()

    configuration = DHCPServerConfiguration()
    configuration.debug = debug_msg
    configuration.adjust_if_this_computer_is_a_router()
    configuration.load(sys.argv[1])
    configuration.router #+= ['192.168.0.1']
    configuration.ip_address_lease_time = 60
    server = DHCPServer(configuration)
    
    for ip in server.configuration.all_ip_addresses():
        assert ip == server.configuration.network_filter()

    s = server.run_in_thread()
    print("UDP DHCP Server loop running in thread:", s.name)
    
    cserver = ThreadedTCPServer((HOST, PORT), ThreadedTCPRequestHandler)
    with cserver:
        cserver.setEvents(messages)
        cserver.setHosts(server.hosts.db)
        cserver.setConfiguration(configuration)
        # Start a thread with the server -- that thread will then start one
        # more thread for each request
        cserver_thread = threading.Thread(target=cserver.serve_forever)
        # Exit the server thread when the main thread terminates
        cserver_thread.daemon = True
        cserver_thread.start()
        print("Control Server loop running in thread:", cserver_thread.name)

        input("Enter to exit")
        cserver.shutdown()
    
    server.close()
