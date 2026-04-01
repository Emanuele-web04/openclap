// FILE: Package.swift
// Purpose: Defines the native SwiftUI macOS shell that wraps the Python clap daemon.
// Depends on: SwiftUI/AppKit plus the helper CLI built from the repo root.

// swift-tools-version: 6.0

import PackageDescription

let package = Package(
    name: "OpenClapDesktop",
    platforms: [
        .macOS(.v13),
    ],
    products: [
        .executable(name: "OpenClapDesktop", targets: ["OpenClapDesktop"]),
    ],
    targets: [
        .executableTarget(
            name: "OpenClapDesktop",
            path: "Sources/OpenClapDesktop"
        ),
    ]
)
