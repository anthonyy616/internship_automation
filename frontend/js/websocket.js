/**
 * WebSocket client for real-time updates
 */

class WebSocketClient {
    constructor(url) {
        this.url = url;
        this.ws = null;
        this.reconnectAttempts = 0;
        this.reconnectDelay = 2000;
        this.listeners = {
            log: [],
            stats: [],
            job: [],
            status: [],
            agent_event: [],
            history: [],
            connect: [],
            disconnect: []
        };
        this._reconnectTimer = null;
    }

    connect() {
        try {
            this.ws = new WebSocket(this.url);

            this.ws.onopen = () => {
                this.reconnectAttempts = 0;
                this._emit('connect');
            };

            this.ws.onclose = () => {
                this._emit('disconnect');
                this._attemptReconnect();
            };

            this.ws.onerror = () => {
                // onclose fires right after onerror; let it drive reconnects.
                // Nothing to do here — the server may be restarting.
            };

            this.ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    this._handleMessage(data);
                } catch (e) {
                    // Handle non-JSON messages (like pong)
                    if (event.data === 'pong') {
                        // keep-alive reply — nothing to do
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
            case 'agent_event':
                this._emit('agent_event', data.event || data);
                break;
            case 'history':
                this._emit('history', data.events || []);
                break;
            case 'input_request':
                this._emit('input_request', data);
                break;
            default:
                // Ignore unknown types silently (server may add new ones)
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
        if (this._reconnectTimer) return;
        if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) return;

        this.reconnectAttempts++;
        // Exponential backoff capped at ~15s + jitter. Never gives up:
        // the server restarts often during development (uvicorn --reload).
        const base = Math.min(15000, 1000 * Math.pow(1.6, this.reconnectAttempts - 1));
        const delay = base + Math.floor(Math.random() * 800);
        // (reconnect happens silently — the activity feed keeps working)

        this._reconnectTimer = setTimeout(() => {
            this._reconnectTimer = null;
            if (this.ws && this.ws.readyState === WebSocket.CLOSED) {
                this.connect();
            }
        }, delay);
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
        if (this._reconnectTimer) {
            clearTimeout(this._reconnectTimer);
            this._reconnectTimer = null;
        }
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
