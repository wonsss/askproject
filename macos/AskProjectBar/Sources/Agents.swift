// 상시 실행 작업(봇·콘솔·매일 백업). 배포 앱은 번들의 Contents/Library/LaunchAgents plist를 SMAppService로 등록한다.
// plist의 BundleProgram이 번들 기준 상대 경로라 앱을 옮기거나 업데이트해도 경로가 깨지지 않는다.
// 개발 앱은 기존처럼 `askproject host on|off`(~/Library/LaunchAgents에 uv run plist)를 쓴다.

import AppKit
import Foundation
import ServiceManagement

enum Agents {
    static let names = ["bot", "console", "backup"]

    static func label(_ name: String) -> String { "com.askproject.\(name)" }

    static func service(_ name: String) -> SMAppService {
        SMAppService.agent(plistName: "\(label(name)).plist")
    }

    static var anyEnabled: Bool { names.contains { service($0).status == .enabled } }

    static var needsApproval: Bool { names.contains { service($0).status == .requiresApproval } }

    /// 켠다. 사용자가 로그인 항목에서 허용해야 하면 true(시스템 설정을 연다).
    @discardableResult
    static func enableAll() throws -> Bool {
        for n in names where service(n).status != .enabled {
            try service(n).register()
        }
        if needsApproval {
            SMAppService.openSystemSettingsLoginItems()
            return true
        }
        return false
    }

    static func disableAll() throws {
        for n in names where service(n).status == .enabled || service(n).status == .requiresApproval {
            try service(n).unregister()
        }
    }

    /// 업데이트 직전: 실행 중인 백엔드를 멈춘다. 파이썬은 모듈을 필요할 때 읽으므로, 교체 중인 번들에서
    /// 새·옛 파일이 섞여 읽히지 않게 한다. 등록(로그인 항목 허용)은 그대로 둔다.
    static func stopForUpdate() {
        for n in names {
            launchctl(["bootout", "gui/\(getuid())/\(label(n))"])
        }
    }

    /// 업데이트 뒤 첫 실행: 멈춰 둔 작업을 새 번들로 다시 띄운다.
    static func reloadEnabled() {
        for n in names where service(n).status == .enabled {
            try? service(n).unregister()
            try? service(n).register()
        }
    }

    static func restart(_ name: String) {
        launchctl(["kickstart", "-k", "gui/\(getuid())/\(label(name))"])
    }

    @discardableResult
    private static func launchctl(_ args: [String]) -> Int32 {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        p.arguments = args
        p.standardOutput = FileHandle.nullDevice
        p.standardError = FileHandle.nullDevice
        try? p.run()
        p.waitUntilExit()
        return p.terminationStatus
    }
}
