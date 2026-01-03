/**
 * WebSocket client for real-time updates
 */

class WebSocketClient {
    constructor(url) {
        this.url = url;
        this.ws = null;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 10;
        this.reconnectDelay = 2000;
        this.listeners = {
            log: [],
            stats: [],
            job: [],
            status: [],
            connect: [],
            disconnect: []
        };
    }

    connect() {
        try {
            this.ws = new WebSocket(this.url);

            this.ws.onopen = () => {
                console.log('WebSocket connected');
                this.reconnectAttempts = 0;
                this._emit('connect');
            };

            this.ws.onclose = () => {
                console.log('WebSocket disconnected');
                this._emit('disconnect');
                this._attemptReconnect();
            };

            this.ws.onerror = (error) => {
                console.error('WebSocket error:', error);
            };

            this.ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    this._handleMessage(data);
                } catch (e) {
                    // Handle non-JSON messages (like pong)
                    if (event.data === 'pong') {
                        console.log('Pong received');
                    }
                }
            };
        } catch (error) {
            console.error('Failed to create WebSocket:', error);
            this._attemptReconnect();
        }
    }

    _handleMessage(data) {
        const type = data.type;

        switch (type) {
            case 'log':
                this._emit('log', data);
                break;
            case 'stats':
                this._emit('stats', data.data);
                break;
            case 'job':
                this._emit('job', data);
                break;
            case 'status':
                this._emit('status', data);
                break;
            default:
                console.log('Unknown message type:', type, data);
        }
    }

    _emit(event, data = null) {
        if (this.listeners[event]) {
            this.listeners[event].forEach(callback => callback(data));
        }
    }

    on(event, callback) {
        if (this.listeners[event]) {
            this.listeners[event].push(callback);
        }
    }

    off(event, callback) {
        if (this.listeners[event]) {
            this.listeners[event] = this.listeners[event].filter(cb => cb !== callback);
        }
    }

    _attemptReconnect() {
        if (this.reconnectAttempts < this.maxReconnectAttempts) {
            this.reconnectAttempts++;
            console.log(`Reconnecting... Attempt ${this.reconnectAttempts}`);

            setTimeout(() => {
                this.connect();
            }, this.reconnectDelay * this.reconnectAttempts);
        } else {
            console.error('Max reconnection attempts reached');
        }
    }

    send(message) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(typeof message === 'string' ? message : JSON.stringify(message));
        }
    }

    ping() {
        this.send('ping');
    }

    disconnect() {
        if (this.ws) {
            this.ws.close();
        }
    }

    get isConnected() {
        return this.ws && this.ws.readyState === WebSocket.OPEN;
    }
}

// Create and export WebSocket client instance
const wsUrl = `ws://${window.location.host}/ws`;
const wsClient = new WebSocketClient(wsUrl);
