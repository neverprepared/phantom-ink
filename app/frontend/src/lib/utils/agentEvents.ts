// Kept as a named entry point for existing importers; the implementation now
// lives in wailsTopic.ts, which this file and collectEvents.ts had each
// reimplemented byte-for-byte apart from the topic string.
//
// Do NOT call Wails' EventsOff('agent:event') -- it removes every subscriber's
// handler. Drop your callback instead (the returned unsubscribe does that).
export { onAgentEvent } from './wailsTopic';
