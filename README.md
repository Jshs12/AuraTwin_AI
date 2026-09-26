AuraTwin AI

AuraTwin AI is a local software prototype for occupancy-informed HVAC advisory and building-energy workflows.

The current prototype connects occupancy inputs, zone state, recommendation logic, safety validation, simulated building control/HVAC response, simulated energy telemetry, and a React dashboard.

Prototype boundary: The current demo is not connected to a physical building, BACnet/IP device, production camera, or energy meter. Demo energy, HVAC response, control acknowledgements, and Demo Mode occupancy are software simulations.

Current Architecture
Repository Structure
backend/ — FastAPI backend and provider/service architecture
frontend/ — React + TypeScript + Vite dashboard
data/building/zones.json — configured building zones
data/test_images/ — local test images
docs/ — architecture, demo, and system-status documentation
tests/ — backend unit/integration tests
yolov8n.pt — local YOLO model used by the YOLO occupancy provider
.env.example — safe configuration template
AURATWIN_V2_TRACKER.md — project implementation tracker
Prerequisites

Install:

Git
Python
Node.js and npm

The project dependencies are declared in requirements.txt and frontend/package.json.

1. Clone the Repository
2. Backend Setup
Windows CMD
macOS/Linux
3. Environment Configuration

Create the local environment file from the template.

Windows CMD
macOS/Linux

For the stable local demo, keep:

To use local YOLO image inference instead of the mock occupancy provider, change:

The repository contains yolov8n.pt, so the configured model path is project-relative.

Security: Never commit .env. Put real API keys, camera URLs, credentials, or other secrets only in your local .env.

4. Start the Backend

From the repository root, with the virtual environment activated:

Backend health check:

Useful status endpoints:

5. Start the Frontend

Open a second terminal.

From the repository root:

Then open the Vite URL shown by the terminal, normally:

For subsequent runs, after dependencies are installed:

6. Run Demo Mode

Start both the backend and frontend.

In the dashboard, use:

AURATWIN DEMO MODE → START

The deterministic scenario progresses through:

Low occupancy
Occupancy rise
High occupancy
Occupancy fall
Completion

Demo controls include:

START
PAUSE
RESUME
STOP
RESET
0.5x / 1x / 2x / 5x speed

The demo exercises the recommendation, safety-validation, control, HVAC-response, energy-telemetry, event, and WebSocket paths.

Demo Mode is simulation only. Its occupancy values are not camera measurements and its energy values are not meter readings.

7. Test YOLO Image Inference

With the backend running and:

use the dashboard's occupancy upload panel or the API:

Supported image types:

JPG
JPEG
PNG
WEBP

YOLO detects the person class.

Local inference verifies that the model executes. It does not establish production accuracy, camera coverage, or real-time site performance.

8. Run Backend Tests

From the repository root with the virtual environment activated:

9. Build the Frontend

Optional lint:

Provider Status
Capability	Current Implementation
Occupancy	Mock / YOLO
Camera	Mock / RTSP adapter
Temperature	Mock / simulated HVAC feedback
Energy	Simulated/mock telemetry
Tariff	Flat mock tariff
Intelligence	Mock / Lyzr adapter
Building Control	Simulated BACnet provider
HVAC	Deterministic simulator
Optimization	Deterministic rule-based optimizer
Dashboard	React + TypeScript + Vite
Safety Boundary

The recommendation path includes validation for:

zone matching
allowed action
finite numeric setpoints/confidence
confidence threshold
global and zone comfort bounds
maximum setpoint change
recommendation freshness
current control-provider readiness

Control decisions are revalidated against fresh state immediately before a provider write.

These checks are software safeguards for the prototype. They are not a substitute for commissioned controls engineering, site-specific interlocks, cybersecurity review, or production HVAC safety procedures.

Current Limitations

The following remain unverified or are not implemented in the current prototype:

Live Lyzr service verification
Production RTSP/camera deployment
Production YOLO accuracy evaluation
Physical BACnet/IP communication
Physical HVAC equipment integration
Real energy-meter integration
Verified energy savings
Durable telemetry/event storage
Production authentication/authorization
Site commissioning
Comprehensive comfort-violation analytics
Production building-level optimization

No real-world energy-saving percentage should be inferred from the demo.

Team Development Workflow

Recommended workflow:

Create a feature branch before making a substantial change:

After development:

Then open a Pull Request into main.

Keep .env, credentials, camera secrets, generated caches, node_modules, and other local-only files out of Git.

Documentation

See:

docs/demo_mode.md — Demo Mode behavior and simulation boundaries
docs/bacnet_control_architecture.md — control-provider architecture
docs/final_system_status.md — current integration/readiness status
License

No project license has been specified yet.