import asyncio
import json
import quart
from contextvars import ContextVar
import time
import sys

def serialize(obj):
    if hasattr(obj, 'to_plotly_json'):
        return serialize(obj.to_plotly_json())
    if isinstance(obj, dict):
        res = {}
        for key in obj:
            res[key] = serialize(obj[key])
        return res
    if isinstance(obj, list) or isinstance(obj, tuple):
        res = []
        for i in obj:
            res.append(serialize(i))
        return res
    return obj


context_rcount = ContextVar('context_rcount')

# This class defers the creation of the asyncio Lock until it's needed,
# to prevent issues with creating the lock before the event loop is running.
# It also handles recurrence/recursion. 
class Alock:
    def __init__(self):
        self.lock = None

    async def __aenter__(self):
        if self.lock is None:
            self.lock = asyncio.Lock()
        try: 
            rcount = context_rcount.get()
        except LookupError:    
            # This is the first acquisition in this context, so get lock.  
            await self.lock.acquire()
            context_rcount.set(1)
            return self.lock
        if rcount==0:
            # We've locked and unlocked within this context already. We need to reacquire lock.
            await self.lock.acquire()

        context_rcount.set(rcount + 1)
        return self.lock

    async def __aexit__(self, exc_type, exc_value, traceback):
        rcount = context_rcount.get() - 1
        context_rcount.set(rcount)
        if rcount==0:
            self.lock.release()

    def locked(self):
        if self.lock is None:
            return False
        return self.lock.locked()


class Client(object):
    def __init__(self):
        self.send_queue = asyncio.Queue()
        self.connect_time = time.time()
        self.address = quart.websocket.remote_addr
        self.host = quart.websocket.host
        self.authentication = None


def exception_handler(loop, context):
    task = context['future']
    task.print_stack()

class Pusher(object):

    def __init__(self, server):
        self.server = server
        self.clients = []
        self.loop = None
        self.url_map = {}
        self.lock = None

        # websocket connection handler 
        @self.server.websocket('/_push')
        async def update_component_socket():
            print('**** spawning')
            # Quart creates the event loop.  This is the best place to grab it (I think).
            if self.loop is None:
                self.loop = asyncio.get_event_loop()
                self.loop.set_exception_handler(exception_handler)
            client = Client()
            self.clients.append(client)
            socket_sender = asyncio.create_task(quart.copy_current_websocket_context(self.socket_sender)(client))
            socket_receiver = asyncio.create_task(quart.copy_current_websocket_context(self.socket_receiver)(client))
            try:
                await asyncio.gather(socket_sender, socket_receiver)
            finally:
                self.clients.remove(client)
                print('*** exitting')


    async def socket_receiver(self, client):
        print('*** ws receive')
        try:
            while True:
                data = await quart.websocket.receive()
                data = json.loads(data);
                # Create new task so we can handle more messages and keep things snappy.
                asyncio.create_task(quart.copy_current_websocket_context(self.dispatch)(data, client))
        except asyncio.CancelledError:
            pass
        print("*** ws receive exit")


    async def socket_sender(self, client):
        print('*** ws send')
        try:
            while True:
                mod = await client.send_queue.get()
                try:
                    json_ = json.dumps(mod)
                except TypeError:
                    json_ = json.dumps(serialize(mod)) 
                await quart.websocket.send(json_)
        except asyncio.CancelledError:
            pass
        print("*** ws send exit")


    async def dispatch(self, data, client):
        index = data['url']
        if index.startswith('/'):
            index = index[1:]

        print('*** url', index, data['id'], data['data'])
        func = self.url_map[index]
        await func(data['data'], client, data['id'])

    async def respond(self, data, request_id):
        assert request_id is not None
        data = {'id': request_id, 'data': data}
        try:
            json_ = json.dumps(data)
        except TypeError:
            json_ = json.dumps(serialize(data)) 
        await quart.websocket.send(json_)

    def add_url(self, url, callback):
        self.url_map[url] = callback

    async def send(self, id_, data, client=None, x_client=None):
        message = {'id': id_, 'data': data}

        # send by putting in event loop
        # Oddly, push_nowait doesn't get serviced right away, so we use asyncio.run_coroutine_threadsafe
        if client is None: # send to all clients
            for client in self.clients:
                if client is not x_client:
                    await client.send_queue.put(message)
        else:
            await client.send_queue.put(message)
        