
const Pusher = {
	
	index: 0,

	add: function() {
		console.log('add ' + Pusher.index);
		Pusher.index++;
	}
}

export function pusherAdd() {
	Pusher.add();
}

