
const pusher = {
	
	setProps: {},

	socket: null, 

    pending: {}, 

	add: function(props, setProps) {

		if (pusher.socket===null) {
			console.log('open socket');
			let url = 'ws://' + document.domain + ':' + location.port + '/_dash-update-component-socket';
	  		pusher.socket = new WebSocket(url);
	  		pusher.socket.onmessage = pusher.receive;
	  		pusher.socket.onclose = pusher.close;
		}
		// add to table
		pusher.setProps[props.id] = setProps;	
	},

	receive: function(event) {
		console.log('receive')
	    console.log(event.data);
        pusher.update(JSON.parse(event.data));
	},	

    baz: function baz() {
        pusher.pending['dummy6.children']({multi: true, response: {dummy6: {children: null}}});
    },

    callback: function(payload) {
        console.log(payload)
        //pusher.socket.send(JSON.stringify(payload));
        const p = new Promise(resolve => pusher.pending[payload.output] = resolve);
        setTimeout(pusher.baz, 2000);
        return p;
    },

	close: function(event) {
		console.log('close socket');
		pusher.socket = null;
	},

    update: function(data) {
        let ids = Object.keys(data);
        for (const id of ids) {
            if (id in pusher.setProps)
            	pusher.setProps[id](data[id], false);
            else
            	console.log('cannot find ' + id);
        }
    },
}

export function pusherAdd(props, setProps) {
	pusher.add(props, setProps);
}

export function pusherCallback(payload) {
    return pusher.callback(payload);   
}