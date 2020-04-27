import asyncio
import json
import quart

class Pusher:

    def __init__(self, server):
        self.server = server
        self.send_queues = []
        self.loop = None

        # websocket connection handler 
        @self.server.websocket('/_push')
        async def update_component_socket():
            print('**** spawning')
            if self.loop is None:
                self.loop = asyncio.get_event_loop()
            queue = asyncio.Queue()
            self.send_queues.append(queue)
            socket_sender = asyncio.create_task(quart.copy_current_websocket_context(self.socket_sender)(queue))
            socket_receiver = asyncio.create_task(quart.copy_current_websocket_context(self.socket_receiver)())
            try:
                await asyncio.gather(socket_sender, socket_receiver)
            finally:
                self.send_queues.remove(queue)
                print('*** exitting')


    async def socket_receiver(self):
        print('*** ws receive')
        try:
            while True:
                print('*** receiving')
                data = await quart.websocket.receive()
                print('receive', data)
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


    def send(self, data):
        for q in self.send_queues:
            asyncio.run_coroutine_threadsafe(q.put(data), self.loop)