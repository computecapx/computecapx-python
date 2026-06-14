ComputeCapX SDKEnterprise-grade FinOps telemetry and AI instrumentation library. ComputeCapX allows developers to monitor AI API usage, enforce budgetary policies, and correlate infrastructure state with application throughput.InstallationInstall the library via pip:pip install computecapx
Quick Start1. Initialize ConfigurationUse the CLI to configure your administrative credentials:computecapx login --key YOUR_API_KEY
2. Instrument AI ClientsAdd the instrumentation layer to your AI workflows to begin telemetry collection:import openai
import computecapx

# Initialize the instrumentation
computecapx.instrument(
    api_key="YOUR_API_KEY", 
    project_id="your_project_id", 
    openai_client=openai
)

# Standard OpenAI calls are now automatically instrumented
response = openai.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Hello, world!"}]
)
FeaturesAutomated Telemetry: Zero-friction collection of token usage and cost metrics.Environment Detection: Autonomous identification of AWS, DigitalOcean, and local compute environments.Asynchronous Transmission: Non-blocking telemetry reporting via background threads.Policy Enforcement: Synchronous budget evaluation to prevent runaway financial leaks.LicenseCopyright © 2026 ComputeCap Engineering. All rights reserved.