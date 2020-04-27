import asyncio
import json
import quart

class Pusher:

    def __init__(self, server):
        self.server = server
        self.clients = []
        self.loop = None
        self.url_map = {}

        # websocket connection handler 
        @self.server.websocket('/_push')
        async def update_component_socket():
            print('**** spawning')
            if self.loop is None:
                self.loop = asyncio.get_event_loop()
            queue = asyncio.Queue()
            self.clients.append(queue)
            socket_sender = asyncio.create_task(quart.copy_current_websocket_context(self.socket_sender)(queue))
            socket_receiver = asyncio.create_task(quart.copy_current_websocket_context(self.socket_receiver)())
            try:
                await asyncio.gather(socket_sender, socket_receiver)
            finally:
                self.clients.remove(queue)
                print('*** exitting')


    async def socket_receiver(self):
        print('*** ws receive')
        try:
            while True:
                print('*** receiving')
                data = await quart.websocket.receive()
                data = json.loads(data);
                print('receive', data)
                await self.dispatch(data)
        except asyncio.CancelledError:
            raise
        finally:
            print("*** ws receive exit")


    async def socket_sender(self, queue):
        print('*** ws send', queue)
        try:
            while True:
                print('*** sending')
                mod = await queue.get()
                await quart.websocket.send(json.dumps(mod))
        except asyncio.CancelledError:
            raise
        finally:
            print("*** ws send exit")


    async def dispatch(self, data):
        func = self.url_map[data['url']]
        output = await func(data['data'])
        output = {'id': data['id'], 'data': output}
        await quart.websocket.send(json.dumps(output))

    def add_url(self, url, callback):
        self.url_map[url] = callback

    def send(self, id_, data, client=None):
        message = {'id': id_, 'data': data}

        # send by putting in event loop
        # Oddly, push_nowait doesn't get serviced right away, so we use asyncio.run_coroutine_threadsafe
        if client is None: # send to all clients
            for client in self.clients:
                asyncio.run_coroutine_threadsafe(client.put(message), self.loop)
        else:
            asyncio.run_coroutine_threadsafe(client.put(message), self.loop)
        