// FILE: HelperBridge.swift
// Purpose: Locates and invokes the bundled/source Python helper so the native shell can stay thin.
// Depends on: Foundation, AppKit, and the helper CLI commands exposed by main.py.

import AppKit
import Foundation

// ─── Wire Models ─────────────────────────────────────────────

struct HelperConfigPayload: Decodable {
    let helperExecutable: String?
    let sourceRoot: String?
}

struct OpenClapConfigPayload: Decodable {
    struct AppSettings: Decodable {
        let launchAtLogin: Bool
        let diagnosticsEnabled: Bool

        private enum CodingKeys: String, CodingKey {
            case launchAtLogin = "launch_at_login"
            case diagnosticsEnabled = "diagnostics_enabled"
        }
    }

    struct ServiceSettings: Decodable {
        let armed: Bool
        let armedOnLaunch: Bool
        let inputDeviceName: String?
        let sensitivityPreset: String

        private enum CodingKeys: String, CodingKey {
            case armed
            case armedOnLaunch = "armed_on_launch"
            case inputDeviceName = "input_device_name"
            case sensitivityPreset = "sensitivity_preset"
        }
    }

    struct ActionSettings: Decodable {
        let targetAppPath: String
        let targetAppName: String
        let localAudioFile: String
        let fallbackMediaURL: String

        private enum CodingKeys: String, CodingKey {
            case targetAppPath = "target_app_path"
            case targetAppName = "target_app_name"
            case localAudioFile = "local_audio_file"
            case fallbackMediaURL = "fallback_media_url"
        }
    }

    let app: AppSettings
    let service: ServiceSettings
    let actions: ActionSettings
}

struct StatusResponse: Decodable {
    struct DetectionHistoryEntry: Decodable, Identifiable {
        let timestamp: Double
        let outcome: String
        let reason: String
        let confidence: Double
        let clapScore: Double
        let signalQuality: String
        let environmentQuality: String
        let source: String
        let status: String

        var id: String { "\(timestamp)-\(outcome)-\(reason)" }

        private enum CodingKeys: String, CodingKey {
            case timestamp, outcome, reason, confidence, source, status
            case clapScore = "clap_score"
            case signalQuality = "signal_quality"
            case environmentQuality = "environment_quality"
        }
    }

    struct EnvironmentSummary: Decodable {
        let signalQuality: String
        let environmentQuality: String
        let lastDetectionConfidence: Double
        let lastRejectionReason: String
        let overflowCount: Int

        private enum CodingKeys: String, CodingKey {
            case signalQuality = "signal_quality"
            case environmentQuality = "environment_quality"
            case lastDetectionConfidence = "last_detection_confidence"
            case lastRejectionReason = "last_rejection_reason"
            case overflowCount = "overflow_count"
        }
    }

    struct Actions: Decodable {
        let targetAppPath: String
        let targetAppName: String
        let localAudioFile: String
        let fallbackMediaURL: String

        private enum CodingKeys: String, CodingKey {
            case targetAppPath = "target_app_path"
            case targetAppName = "target_app_name"
            case localAudioFile = "local_audio_file"
            case fallbackMediaURL = "fallback_media_url"
        }
    }

    let armed: Bool
    let launchAtLogin: Bool
    let diagnosticsEnabled: Bool
    let armedOnLaunch: Bool
    let deviceName: String
    let inputDeviceName: String?
    let detectorStatus: String
    let signalQuality: String
    let environmentQuality: String
    let sensitivityPreset: String
    let lastTriggerAt: Double?
    let lastError: String
    let lastTriggerSource: String
    let calibrationState: String
    let lastCalibratedAt: Double?
    let actions: Actions
    let recentDetectionHistory: [DetectionHistoryEntry]
    let environmentSummary: EnvironmentSummary

    private enum CodingKeys: String, CodingKey {
        case armed, actions
        case launchAtLogin = "launch_at_login"
        case diagnosticsEnabled = "diagnostics_enabled"
        case armedOnLaunch = "armed_on_launch"
        case deviceName = "device_name"
        case inputDeviceName = "input_device_name"
        case detectorStatus = "detector_status"
        case signalQuality = "signal_quality"
        case environmentQuality = "environment_quality"
        case sensitivityPreset = "sensitivity_preset"
        case lastTriggerAt = "last_trigger_at"
        case lastError = "last_error"
        case lastTriggerSource = "last_trigger_source"
        case calibrationState = "calibration_state"
        case lastCalibratedAt = "last_calibrated_at"
        case recentDetectionHistory = "recent_detection_history"
        case environmentSummary = "environment_summary"
    }
}

struct DeviceListResponse: Decodable {
    struct Device: Decodable, Hashable, Identifiable {
        let index: Int
        let name: String

        var id: String { "\(index)-\(name)" }
    }

    let devices: [Device]
}

// ─── Helper Invocation ───────────────────────────────────────

struct HelperInvocation {
    let executable: String
    let baseArguments: [String]
}

enum HelperBridgeError: LocalizedError {
    case missingHelper
    case commandFailed(String)

    var errorDescription: String? {
        switch self {
        case .missingHelper:
            return "OpenClap could not locate its helper runtime."
        case .commandFailed(let message):
            return message
        }
    }
}

final class HelperBridge: @unchecked Sendable {
    private let decoder = JSONDecoder()
    private let invocation: HelperInvocation

    init() throws {
        self.invocation = try Self.resolveInvocation()
    }

    // ─── Data Queries ─────────────────────────────────────────

    func loadConfig() throws -> OpenClapConfigPayload {
        try runJSON(["config", "--json"], as: OpenClapConfigPayload.self)
    }

    func loadStatus() throws -> StatusResponse {
        try runJSON(["status", "--json"], as: StatusResponse.self)
    }

    func listDevices() throws -> [DeviceListResponse.Device] {
        try runJSON(["list-devices", "--json"], as: DeviceListResponse.self).devices
    }

    // ─── Mutations / Commands ─────────────────────────────────

    func arm() throws { _ = try run(["arm"]) }
    func disarm() throws { _ = try run(["disarm"]) }
    func reloadConfig() throws { _ = try run(["reload-config"]) }
    func startCalibration() throws { _ = try run(["start-calibration"]) }
    func testTrigger() throws { _ = try run(["test-trigger"]) }
    func setSensitivity(_ preset: String) throws { _ = try run(["set-sensitivity", preset]) }
    func setTargetApp(path: String) throws { _ = try run(["set-target-app", path]) }
    func clearTargetApp() throws { _ = try run(["clear-target-app"]) }
    func setInputDevice(name: String) throws { _ = try run(["set-input-device", name]) }
    func setArmedOnLaunch(_ value: Bool) throws { _ = try run(["set-armed-on-launch", value ? "true" : "false"]) }
    func setDiagnosticsEnabled(_ value: Bool) throws { _ = try run(["set-diagnostics-enabled", value ? "true" : "false"]) }

    func setLaunchAtLogin(_ value: Bool) throws {
        var command = ["set-launch-at-login", value ? "true" : "false"]
        if let bundlePath = Bundle.main.bundleURL.bundleURLIfAppBundle?.path {
            command.append(contentsOf: ["--companion-app", bundlePath])
        }
        _ = try run(command)
    }

    func bootstrapNativeShell() throws {
        var command = ["bootstrap-native-shell"]
        if let bundlePath = Bundle.main.bundleURL.bundleURLIfAppBundle?.path {
            command.append(contentsOf: ["--companion-app", bundlePath])
        }
        _ = try run(command)
    }

    // ─── Utilities ────────────────────────────────────────────

    func openLogsFolder() {
        let logsURL = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Logs/OpenClap", isDirectory: true)
        NSWorkspace.shared.open(logsURL)
    }

    func openConfigFolder() {
        let configURL = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/OpenClap", isDirectory: true)
        NSWorkspace.shared.open(configURL)
    }

    private func runJSON<T: Decodable>(_ arguments: [String], as type: T.Type) throws -> T {
        let output = try run(arguments)
        guard let data = output.data(using: .utf8) else {
            throw HelperBridgeError.commandFailed("OpenClap helper returned invalid UTF-8 output.")
        }
        return try decoder.decode(type, from: data)
    }

    private func run(_ arguments: [String]) throws -> String {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: invocation.executable)
        process.arguments = invocation.baseArguments + arguments
        process.currentDirectoryURL = Bundle.main.bundleURL.bundleURLIfAppBundle?.deletingLastPathComponent()
        process.environment = ProcessInfo.processInfo.environment

        let outputPipe = Pipe()
        let errorPipe = Pipe()
        process.standardOutput = outputPipe
        process.standardError = errorPipe

        try process.run()
        process.waitUntilExit()

        let outputData = outputPipe.fileHandleForReading.readDataToEndOfFile()
        let errorData = errorPipe.fileHandleForReading.readDataToEndOfFile()
        let output = String(data: outputData, encoding: .utf8) ?? ""
        let error = String(data: errorData, encoding: .utf8) ?? ""

        guard process.terminationStatus == 0 else {
            let message = error.isEmpty ? output.trimmingCharacters(in: .whitespacesAndNewlines) : error.trimmingCharacters(in: .whitespacesAndNewlines)
            throw HelperBridgeError.commandFailed(message.isEmpty ? "OpenClap helper command failed." : message)
        }

        return output
    }

    private static func resolveInvocation() throws -> HelperInvocation {
        if
            let configURL = Bundle.main.url(forResource: "HelperConfig", withExtension: "json"),
            let data = try? Data(contentsOf: configURL),
            let payload = try? JSONDecoder().decode(HelperConfigPayload.self, from: data)
        {
            if let executable = payload.helperExecutable, let resourcesURL = Bundle.main.resourceURL {
                let helperPath = resourcesURL.appendingPathComponent(executable).path
                if FileManager.default.isExecutableFile(atPath: helperPath) {
                    return HelperInvocation(executable: helperPath, baseArguments: [])
                }
            }

            if let sourceRoot = payload.sourceRoot {
                return try resolveSourceInvocation(sourceRoot: sourceRoot)
            }
        }

        return try resolveSourceInvocation(sourceRoot: FileManager.default.currentDirectoryPath)
    }

    private static func resolveSourceInvocation(sourceRoot: String) throws -> HelperInvocation {
        let rootURL = URL(fileURLWithPath: sourceRoot, isDirectory: true)
        let localPython = rootURL.appendingPathComponent(".venv/bin/python").path
        let pythonExecutable = FileManager.default.isExecutableFile(atPath: localPython) ? localPython : "/usr/bin/python3"
        let mainPath = rootURL.appendingPathComponent("main.py").path
        guard FileManager.default.fileExists(atPath: mainPath) else {
            throw HelperBridgeError.missingHelper
        }
        return HelperInvocation(executable: pythonExecutable, baseArguments: [mainPath])
    }
}

private extension URL {
    var bundleURLIfAppBundle: URL? {
        pathExtension == "app" ? self : nil
    }
}
