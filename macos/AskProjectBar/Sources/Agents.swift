// 상시 실행 작업(봇·콘솔·매일 백업, 그리고 켰을 때만 팀 공유 터널). 배포 앱은 번들의 Contents/Library/LaunchAgents plist를 SMAppService로 등록한다.
// plist의 BundleProgram이 번들 기준 상대 경로라 앱을 옮기거나 업데이트해도 경로가 깨지지 않는다.
// 개발 앱은 기존처럼 `askproject host on|off`(~/Library/LaunchAgents에 uv run plist)를 쓴다.

import AppKit
import Foundation
import ServiceManagement

enum Agents {
    static let names = ["bot", "console", "backup"]
    /// 팀 공유(웹 콘솔 Cloudflare 터널). 봇 켜기·끄기와 따로 설정 → 팀 공유에서만 켜고 끈다.
    static let tunnel = "tunnel"
    static var all: [String] { names + [tunnel] }

    static func label(_ name: String) -> String { "com.askproject.\(name)" }

    static func service(_ name: String) -> SMAppService {
        SMAppService.agent(plistName: "\(label(name)).plist")
    }

    static var anyEnabled: Bool { names.contains { service($0).status == .enabled } }

    static var needsApproval: Bool { names.contains { service($0).status == .requiresApproval } }

    /// 켠다. 사용자가 로그인 항목에서 허용해야 하면 true(시스템 설정을 연다).
    @discardableResult
    static func enableAll() throws -> Bool {
        // 등록은 됐는데 bootout된 작업(업데이트 취소·마이그레이션 실패 뒤)은 떠 있지 않다 — 다시 등록해 띄운다
        resume(names.filter { service($0).status == .enabled })
        for n in names where service(n).status != .enabled {
            try service(n).register()
        }
        if needsApproval {
            SMAppService.openSystemSettingsLoginItems()
            return true
        }
        return false
    }

    static var tunnelEnabled: Bool { service(tunnel).status == .enabled }

    /// 팀 공유 켜기·끄기. 켤 때 로그인 항목 허용이 필요하면 true(시스템 설정을 연다).
    @discardableResult
    static func setTunnel(_ on: Bool) throws -> Bool {
        let s = service(tunnel)
        if on {
            if s.status != .enabled { try s.register() }
            if s.status == .requiresApproval {
                SMAppService.openSystemSettingsLoginItems()
                return true
            }
            restart(tunnel)  // 이미 켜져 있었다면 새 토큰으로 다시 붙는다
        } else if s.status == .enabled || s.status == .requiresApproval {
            try s.unregister()
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
        for n in all {
            launchctl(["bootout", "gui/\(getuid())/\(label(n))"])
        }
    }

    /// 업데이트 뒤 첫 실행: 멈춰 둔 작업을 새 번들로 다시 띄운다.
    static func reloadEnabled() {
        for n in all where service(n).status == .enabled {
            try? service(n).unregister()
            try? service(n).register()
        }
    }

    /// 복구 직전: 켜져 있는(등록된) 작업만 멈추고 그 이름을 돌려준다. 등록은 그대로 둬서 resume으로 다시 띄운다.
    static func pauseEnabled(_ list: [String]) -> [String] {
        let on = list.filter { service($0).status == .enabled }
        for n in on { launchctl(["bootout", "gui/\(getuid())/\(label(n))"]) }
        return on
    }

    /// pauseEnabled로 멈춘 작업을 다시 띄운다(재등록 — bootout한 작업은 kickstart로는 살아나지 않는다).
    static func resume(_ list: [String]) {
        for n in list {
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
        do { try p.run() } catch { return -1 }  // 실행 못 한 Process의 terminationStatus는 예외로 앱을 죽인다
        p.waitUntilExit()
        return p.terminationStatus
    }
}
