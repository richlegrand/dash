
const pushee = {
	
	setProps: {},

	socket: null, 

    pending: {}, 

	add: function(props, setProps) {

		if (pushee.socket===null) {
			console.log('open socket');
			let url = 'ws://' + document.domain + ':' + location.port + '/_push';
	  		pushee.socket = new WebSocket(url);
	  		pushee.socket.onmessage = pushee.receive;
	  		pushee.socket.onclose = pushee.close;
		}
		// add to table
		pushee.setProps[props.id] = setProps;	
	},

	receive: function(event) {
		console.log('receive')
	    console.log(event.data);
        pushee.update(JSON.parse(event.data));
	},	

    baz: function baz() {
        pushee.pending['dummy6.children']({multi: true, response: {dummy6: {children: null}}});
    },

    callback: function(payload) {
        console.log(payload)
        //pushee.socket.send(JSON.stringify(payload));
        const p = new Promise(resolve => pushee.pending[payload.output] = resolve);
        setTimeout(pushee.baz, 2000);
        return p;
    },

	close: function(event) {
		console.log('close socket');
		pushee.socket = null;
	},

    update: function(data) {
        let ids = Object.keys(data);
        for (const id of ids) {
            if (id in pushee.setProps)
            	pushee.setProps[id](data[id], false);
            else
            	console.log('cannot find ' + id);
        }
    },
}

export function pusheeAdd(props, setProps) {
	pushee.add(props, setProps);
}

export function pusheeCallback(payload) {
    return pushee.callback(payload);   
}