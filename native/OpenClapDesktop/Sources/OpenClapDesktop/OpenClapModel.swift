// FILE: OpenClapModel.swift
// Purpose: Owns native app state, polling, and user actions for the SwiftUI shell.
// Depends on: HelperBridge.swift for helper process calls and SwiftUI for published state.

import AppKit
import Foundation
import SwiftUI
import UniformTypeIdentifiers

@MainActor
final class OpenClapModel: ObservableObject {
    @Published private(set) var config: OpenClapConfigPayload?
    @Published private(set) var status: StatusResponse?
    @Published private(set) var devices: [DeviceListResponse.Device] = []
    @Published var errorMessage: String = ""
    @Published private(set) var isRefreshing = false

    private let helper: HelperBridge?
    private var refreshTimer: Timer?

    init() {
        do {
            self.helper = try HelperBridge()
        } catch {
            self.helper = nil
            self.errorMessage = error.localizedDescription
        }
    }

    // ─── Derived View State ───────────────────────────────────

    var isArmed: Bool { status?.armed ?? config?.service.armed ?? false }
    var sensitivityPreset: String { status?.sensitivityPreset ?? config?.service.sensitivityPreset ?? "balanced" }
    var deviceDisplayName: String { status?.deviceName ?? config?.service.inputDeviceName ?? "Default microphone" }
    var targetAppDisplayName: String {
        let targetName = status?.actions.targetAppName ?? config?.actions.targetAppName ?? ""
        if !targetName.isEmpty { return targetName }
        let targetPath = status?.actions.targetAppPath ?? config?.actions.targetAppPath ?? ""
        return targetPath.isEmpty ? "Not selected" : URL(fileURLWithPath: targetPath).deletingPathExtension().lastPathComponent
    }
    var statusLine: String {
        guard let status else { return "Helper offline" }
        if !status.lastError.isEmpty { return status.lastError }
        return "\(status.detectorStatus.capitalized) • \(status.environmentQuality)"
    }
    var launchAtLogin: Bool { status?.launchAtLogin ?? config?.app.launchAtLogin ?? true }
    var armedOnLaunch: Bool { status?.armedOnLaunch ?? config?.service.armedOnLaunch ?? true }
    var diagnosticsEnabled: Bool { status?.diagnosticsEnabled ?? config?.app.diagnosticsEnabled ?? true }
    var lastErrorText: String { status?.lastError.isEmpty == false ? status?.lastError ?? "" : "None" }

    // ─── Polling ──────────────────────────────────────────────

    func start() {
        refresh(allowBootstrap: true)
        refreshTimer?.invalidate()
        refreshTimer = Timer.scheduledTimer(withTimeInterval: 2.0, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
        }
    }

    func refresh(allowBootstrap: Bool = false) {
        guard let helper else { return }
        if isRefreshing { return }

        isRefreshing = true
        DispatchQueue.global(qos: .userInitiated).async {
            let configResult = Result { try helper.loadConfig() }
            let statusResult = Result { try helper.loadStatus() }
            let devicesResult = Result { try helper.listDevices() }

            if allowBootstrap, case .failure = statusResult {
                try? helper.bootstrapNativeShell()
                Thread.sleep(forTimeInterval: 0.8)
            }

            let finalStatusResult = allowBootstrap ? Result { try helper.loadStatus() } : statusResult
            let finalDevicesResult = allowBootstrap ? Result { try helper.listDevices() } : devicesResult

            DispatchQueue.main.async {
                self.isRefreshing = false

                if case .success(let config) = configResult {
                    self.config = config
                }
                if case .success(let status) = finalStatusResult {
                    self.status = status
                }
                if case .success(let devices) = finalDevicesResult {
                    self.devices = devices
                }

                let errors = [
                    Self.errorMessage(from: configResult),
                    Self.errorMessage(from: finalStatusResult),
                    Self.errorMessage(from: finalDevicesResult),
                ].compactMap { $0 }
                self.errorMessage = errors.first ?? ""
            }
        }
    }

    // ─── UI Actions ───────────────────────────────────────────

    func toggleArmed(_ armed: Bool) {
        performMutation { helper in
            if armed {
                try helper.arm()
            } else {
                try helper.disarm()
            }
        }
    }

    func setSensitivity(_ preset: String) {
        performMutation { helper in
            try helper.setSensitivity(preset)
        }
    }

    func setLaunchAtLogin(_ enabled: Bool) {
        performMutation { helper in
            try helper.setLaunchAtLogin(enabled)
        }
    }

    func setArmedOnLaunch(_ enabled: Bool) {
        performMutation { helper in
            try helper.setArmedOnLaunch(enabled)
        }
    }

    func setDiagnosticsEnabled(_ enabled: Bool) {
        performMutation { helper in
            try helper.setDiagnosticsEnabled(enabled)
        }
    }

    func startCalibration() {
        performMutation { helper in
            try helper.startCalibration()
        }
    }

    func testTrigger() {
        performMutation { helper in
            try helper.testTrigger()
        }
    }

    func pickTargetApp() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        panel.allowsMultipleSelection = false
        panel.allowedContentTypes = [.applicationBundle]
        panel.prompt = "Choose App"
        panel.message = "Choose the app OpenClap should launch after a valid double clap."

        guard panel.runModal() == .OK, let url = panel.url else { return }
        performMutation { helper in
            try helper.setTargetApp(path: url.path)
        }
    }

    func clearTargetApp() {
        performMutation { helper in
            try helper.clearTargetApp()
        }
    }

    func selectInputDevice(_ deviceName: String) {
        performMutation { helper in
            try helper.setInputDevice(name: deviceName)
        }
    }

    func openLogsFolder() {
        helper?.openLogsFolder()
    }

    func openConfigFolder() {
        helper?.openConfigFolder()
    }

    // ─── Mutation Helper ──────────────────────────────────────

    private func performMutation(_ work: @escaping @Sendable (HelperBridge) throws -> Void) {
        guard let helper else {
            errorMessage = HelperBridgeError.missingHelper.errorDescription ?? "OpenClap helper is unavailable."
            return
        }

        DispatchQueue.global(qos: .userInitiated).async {
            do {
                try work(helper)
                let updatedConfig = try helper.loadConfig()
                let updatedStatus = try? helper.loadStatus()
                let updatedDevices = try? helper.listDevices()
                DispatchQueue.main.async {
                    self.config = updatedConfig
                    if let updatedStatus { self.status = updatedStatus }
                    if let updatedDevices { self.devices = updatedDevices }
                    self.errorMessage = ""
                }
            } catch {
                DispatchQueue.main.async {
                    self.errorMessage = error.localizedDescription
                }
            }
        }
    }

    private static func errorMessage<T>(from result: Result<T, Error>) -> String? {
        guard case .failure(let error) = result else { return nil }
        return error.localizedDescription
    }
}
