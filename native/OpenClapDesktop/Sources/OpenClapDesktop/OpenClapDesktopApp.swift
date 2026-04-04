// FILE: OpenClapDesktopApp.swift
// Purpose: Declares the native macOS product surface: settings window plus menu bar controls.
// Depends on: OpenClapModel.swift for state and HelperBridge.swift for helper-backed actions.

import AppKit
import SwiftUI

// ─── App Entry ───────────────────────────────────────────────

@main
struct OpenClapDesktopApp: App {
    @NSApplicationDelegateAdaptor(OpenClapAppDelegate.self) private var appDelegate
    @StateObject private var model = OpenClapModel()

    var body: some Scene {
        WindowGroup("OpenClap") {
            SettingsView(model: model)
                .frame(minWidth: 860, minHeight: 620)
                .task { model.start() }
        }
        .defaultSize(width: 940, height: 680)

        MenuBarExtra("OpenClap", systemImage: model.isArmed ? "waveform.circle.fill" : "pause.circle.fill") {
            MenuBarContent(model: model)
        }
        .menuBarExtraStyle(.window)
    }
}

final class OpenClapAppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        guard ProcessInfo.processInfo.environment["OPENCLAP_BACKGROUND_LAUNCH"] == "1" else { return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) {
            NSApp.windows.forEach { $0.orderOut(nil) }
        }
    }
}

// ─── Settings Window ────────────────────────────────────────

struct SettingsView: View {
    @ObservedObject var model: OpenClapModel

    var body: some View {
        NavigationSplitView {
            List {
                Section("Status") {
                    statusSummaryRow(label: "Detector", value: model.status?.detectorStatus.capitalized ?? "Offline")
                    statusSummaryRow(label: "Environment", value: model.status?.environmentQuality.capitalized ?? "Unknown")
                    statusSummaryRow(label: "Signal", value: model.status?.signalQuality.capitalized ?? "Unknown")
                    statusSummaryRow(label: "Device", value: model.deviceDisplayName)
                }
            }
            .listStyle(.sidebar)
        } detail: {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    heroSection
                    generalSection
                    detectionSection
                    diagnosticsSection
                    experimentalSection
                }
                .padding(24)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .background(Color(nsColor: .windowBackgroundColor))
        }
    }

    private var heroSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("OpenClap")
                .font(.system(size: 30, weight: .semibold, design: .rounded))
            Text("Native control room for the always-on clap helper. Tune reliability, watch near-misses, and keep the background daemon honest.")
                .foregroundStyle(.secondary)
            HStack(spacing: 12) {
                Toggle(isOn: Binding(
                    get: { model.isArmed },
                    set: { model.toggleArmed($0) }
                )) {
                    Text(model.isArmed ? "Detection armed" : "Detection paused")
                }
                .toggleStyle(.switch)
                .frame(maxWidth: 220)

                Button("Open Logs") { model.openLogsFolder() }
                Button("Open Config") { model.openConfigFolder() }
                Button("Refresh") { model.refresh() }
            }
            if !model.errorMessage.isEmpty {
                Text(model.errorMessage)
                    .font(.callout)
                    .foregroundStyle(.red)
            }
        }
    }

    private var generalSection: some View {
        GroupBox("General") {
            VStack(alignment: .leading, spacing: 14) {
                Toggle("Start helper at login", isOn: Binding(
                    get: { model.launchAtLogin },
                    set: { model.setLaunchAtLogin($0) }
                ))
                Toggle("Arm detector on launch", isOn: Binding(
                    get: { model.armedOnLaunch },
                    set: { model.setArmedOnLaunch($0) }
                ))
                HStack {
                    Text("Target App")
                    Spacer()
                    Text(model.targetAppDisplayName)
                        .foregroundStyle(.secondary)
                }
                HStack(spacing: 10) {
                    Button("Choose App") { model.pickTargetApp() }
                    Button("Clear") { model.clearTargetApp() }
                        .disabled(model.targetAppDisplayName == "Not selected")
                }

                Picker("Microphone", selection: Binding(
                    get: { model.status?.inputDeviceName ?? model.config?.service.inputDeviceName ?? "" },
                    set: { if !$0.isEmpty { model.selectInputDevice($0) } }
                )) {
                    if (model.status?.inputDeviceName ?? model.config?.service.inputDeviceName) == nil {
                        Text("System default").tag("")
                    }
                    ForEach(model.devices) { device in
                        Text(device.name).tag(device.name)
                    }
                }
                .pickerStyle(.menu)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var detectionSection: some View {
        GroupBox("Detection") {
            VStack(alignment: .leading, spacing: 14) {
                Picker("Sensitivity", selection: Binding(
                    get: { model.sensitivityPreset },
                    set: { model.setSensitivity($0) }
                )) {
                    Text("Balanced").tag("balanced")
                    Text("Responsive").tag("responsive")
                    Text("Sensitive").tag("sensitive")
                    Text("Strict").tag("strict")
                }
                .pickerStyle(.segmented)

                Picker("Backend", selection: Binding(
                    get: { model.detectorBackend },
                    set: { model.setDetectorBackend($0) }
                )) {
                    Text("Native").tag("native")
                    Text("Pector").tag("pector")
                }
                .pickerStyle(.segmented)

                HStack(spacing: 10) {
                    Button("Install Pector") { model.installPector() }
                    Text("External GPL backend, not bundled into the app.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                HStack {
                    metricCard(title: "Last Trigger", value: formattedTimestamp(model.status?.lastTriggerAt))
                    metricCard(title: "Confidence", value: confidenceLabel(model.status?.environmentSummary.lastDetectionConfidence))
                    metricCard(title: "Last Error", value: model.lastErrorText)
                }

                HStack(spacing: 10) {
                    Button("Run Calibration") { model.startCalibration() }
                    Button("Test Trigger") { model.testTrigger() }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var diagnosticsSection: some View {
        GroupBox("Diagnostics") {
            VStack(alignment: .leading, spacing: 14) {
                Toggle("Keep recent detection history", isOn: Binding(
                    get: { model.diagnosticsEnabled },
                    set: { model.setDiagnosticsEnabled($0) }
                ))
                HStack {
                    metricCard(title: "Signal", value: model.status?.signalQuality.capitalized ?? "Unknown")
                    metricCard(title: "Environment", value: model.status?.environmentQuality.capitalized ?? "Unknown")
                    metricCard(title: "Last Rejection", value: rejectionLabel(model.status?.environmentSummary.lastRejectionReason))
                }

                if let history = model.status?.recentDetectionHistory, !history.isEmpty {
                    VStack(alignment: .leading, spacing: 10) {
                        ForEach(history.prefix(8)) { entry in
                            HStack(alignment: .top, spacing: 12) {
                                Text(formattedTimestamp(entry.timestamp))
                                    .font(.caption.monospacedDigit())
                                    .foregroundStyle(.secondary)
                                    .frame(width: 88, alignment: .leading)
                                Text(entry.outcome.capitalized)
                                    .fontWeight(.semibold)
                                    .frame(width: 82, alignment: .leading)
                                Text(entry.reason)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                Text(confidenceLabel(entry.confidence))
                                    .foregroundStyle(.secondary)
                            }
                            Divider()
                        }
                    }
                } else {
                    Text("No detection history yet. Clap a few times or let the daemon reject some near-misses to populate this panel.")
                        .foregroundStyle(.secondary)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var experimentalSection: some View {
        GroupBox("Clap + Wake Word") {
            VStack(alignment: .leading, spacing: 14) {
                Toggle("Require a wake word after the double clap", isOn: Binding(
                    get: { model.voiceConfirmationEnabled },
                    set: { model.setVoiceConfirmationEnabled($0) }
                ))

                HStack {
                    Text("Wake Phrase")
                    Spacer()
                    Text(model.wakePhrase)
                        .foregroundStyle(.secondary)
                    Button("Use \"jarvis\"") { model.setWakePhrase("jarvis") }
                }

                HStack {
                    Text("Keyword File")
                    Spacer()
                    Text(model.wakeKeywordPath.isEmpty ? "Not selected" : URL(fileURLWithPath: model.wakeKeywordPath).lastPathComponent)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
                HStack(spacing: 10) {
                    Button("Choose Keyword") { model.pickWakeKeyword() }
                    Button("Clear") { model.clearWakeKeywordPath() }
                        .disabled(model.wakeKeywordPath.isEmpty)
                }

                HStack {
                    Text("Wake Window")
                    Spacer()
                    Text(String(format: "%.1fs", model.wakeWindowSeconds))
                        .foregroundStyle(.secondary)
                }
                Slider(
                    value: Binding(
                        get: { model.wakeWindowSeconds },
                        set: { model.setWakeWindow(($0 * 10).rounded() / 10) }
                    ),
                    in: 1.0...5.0,
                    step: 0.1
                )

                Text("Flow: clap clap, then say the wake phrase inside the short window. The default is jarvis, but you can still switch to another phrase later if you want.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private func statusSummaryRow(label: String, value: String) -> some View {
        HStack {
            Text(label)
            Spacer()
            Text(value)
                .foregroundStyle(.secondary)
        }
    }

    private func metricCard(title: String, value: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title)
                .font(.caption)
                .foregroundStyle(.secondary)
            Text(value)
                .font(.headline)
                .lineLimit(2)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
    }

    private func formattedTimestamp(_ timestamp: Double?) -> String {
        guard let timestamp else { return "Never" }
        let formatter = DateFormatter()
        formatter.timeStyle = .medium
        formatter.dateStyle = .none
        return formatter.string(from: Date(timeIntervalSince1970: timestamp))
    }

    private func confidenceLabel(_ confidence: Double?) -> String {
        guard let confidence else { return "0%" }
        return "\(Int((confidence * 100.0).rounded()))%"
    }

    private func rejectionLabel(_ reason: String?) -> String {
        guard let reason, !reason.isEmpty else { return "None" }
        return reason.capitalized
    }
}

// ─── Menu Bar Surface ────────────────────────────────────────

struct MenuBarContent: View {
    @ObservedObject var model: OpenClapModel

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(model.isArmed ? "Detector armed" : "Detector paused")
                .font(.headline)
            Text(model.statusLine)
                .foregroundStyle(.secondary)
            Divider()
            Button(model.isArmed ? "Pause Detection" : "Resume Detection") {
                model.toggleArmed(!model.isArmed)
            }
            Button("Run Calibration") { model.startCalibration() }
            Button("Open Settings") {
                NSApp.activate(ignoringOtherApps: true)
                NSApp.windows.first?.makeKeyAndOrderFront(nil)
            }
            Divider()
            Text(model.status?.recentDetectionHistory.first.map { "\($0.outcome.capitalized): \($0.reason)" } ?? "No recent detections")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .padding(14)
        .frame(width: 300)
    }
}
