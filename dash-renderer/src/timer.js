
const timer = {
    n: 0, 

    avg: 0, 

    startTimer: function() {
        return window.performance.now();
    },

    stopTimer: function(t) {
        const diff = window.performance.now() - t;
        timer.n++;
        timer.avg = ((timer.n-1)*timer.avg+diff)/timer.n;
        console.log(timer.n + " " + 
            Number.parseFloat(timer.avg).toFixed(2) + " " + 
            Number.parseFloat(diff).toFixed(2));
    },
};

export function startTimer() {
    return timer.startTimer();
}

export function stopTimer(t) {
    timer.stopTimer(t);
}