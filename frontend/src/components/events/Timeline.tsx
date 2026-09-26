import type { SystemEvent } from "../../types/api";
import { Card } from "../common";
import { formatISTTimestamp } from "../../utils/time";

export function Timeline({ events }: { events: SystemEvent[] }) {
  if (events.length === 0) {
    return (
      <Card title="Event Timeline">
        <div style={{ color: "var(--text-muted)", fontSize: "0.875rem" }}>No event history available.</div>
      </Card>
    );
  }

  return (
    <Card title="Event Timeline">
      <div className="timeline">
        {events.map((event) => {
          const time = formatISTTimestamp(event.timestamp);
          return (
            <div key={event.event_id} className="timeline-event">
              <div className="event-time">{time}</div>
              <div className="event-details">
                <div className="event-type">{event.event_type}</div>
                <div className="event-source">{event.source}</div>
              </div>
            </div>
          );
        })}
      </div>
    </Card>
  );
}
