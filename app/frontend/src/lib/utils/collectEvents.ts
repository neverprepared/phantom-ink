// Kept as a named entry point for existing importers; the implementation now
// lives in wailsTopic.ts (see agentEvents.ts).
//
// Do NOT call Wails' EventsOff('collect:update') -- it removes every
// subscriber's handler. Drop your callback instead.
export { onCollectUpdate } from './wailsTopic';
