
const pusher = {
	
	setProps: {},

	socket: null, 

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

	close: function(event) {
		console.log('close socket');
		pusher.socket = null;
	},

    update: function(data) {
        let ids = Object.keys(data);
        for (const id of ids) {
            if (id in pusher.setProps)
            	pusher.setProps[id](data[id], true);
            else
            	console.log('cannot find ' + id);
        }
    },
}

export function pusherAdd(props, setProps) {
	pusher.add(props, setProps);
}
