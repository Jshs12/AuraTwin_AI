import { useRef } from "react";
import { useOccupancy } from "../../hooks/useOccupancy";
import { Card, OccupancyBadge, Button } from "../common";
import { API_BASE } from "../../services/api";

export function CVPanel({ zoneId }: { zoneId: string }) {
  const { detect, detecting, error, detectionResult } = useOccupancy();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      await detect(file, zoneId);
    }
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  return (
    <Card title="COMPUTER VISION OCCUPANCY — UPLOADED IMAGE INFERENCE">
      <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
        <div className="badge warning" style={{ alignSelf: "flex-start" }}>Manual CV test — not used by Demo Mode</div>
        
        <input 
          type="file" 
          accept="image/jpeg,image/png,image/webp" 
          style={{ display: "none" }} 
          ref={fileInputRef}
          onChange={handleFileChange}
        />
        
        {!detectionResult && (
          <div className="upload-area" onClick={() => fileInputRef.current?.click()}>
            {detecting ? "Running occupancy inference..." : "Click to upload an image for occupancy detection"}
          </div>
        )}

        {error && <div style={{color: "var(--accent-red)", fontSize: "0.875rem"}}>{error}</div>}

        {detectionResult && (
          <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
            <div className="zone-stats">
              <div>
                <div className="stat-label">Detected People</div>
                <div className="stat-value" style={{ fontSize: "1.5rem" }}>{detectionResult.detection.people_count}</div>
                <div style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>Detection count from the configured provider</div>
              </div>
              <div>
                <div className="stat-label">State</div>
                <OccupancyBadge state={detectionResult.occupancy.occupancy_state} />
              </div>
            </div>

            <div className="section-block">
              <div className="stat-label">Model</div>
              <div className="stat-value">{detectionResult.detection.model_name}</div>
            </div>

            {detectionResult.detection.annotated_image_path && (
              <div>
                <div className="stat-label" style={{marginBottom: "0.5rem"}}>Annotated Result</div>
                <img 
                  src={`${API_BASE}/static/${encodeURIComponent(detectionResult.detection.annotated_image_path.split("/").pop() ?? "")}`} 
                  alt="YOLO Detection" 
                  className="preview-image" 
                  onError={(e) => { e.currentTarget.style.display = 'none'; }}
                />
              </div>
            )}
            
            <Button variant="neutral" onClick={() => fileInputRef.current?.click()} disabled={detecting}>
              {detecting ? "Processing..." : "Upload New Image"}
            </Button>
          </div>
        )}
      </div>
    </Card>
  );
}
