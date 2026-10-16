// 앱 업데이트(Sparkle 2). Info.plist의 SUFeedURL·SUPublicEDKey가 있을 때만 켠다.
// 설치 직전 백엔드 작업을 멈추고(Agents.stopForUpdate), 새 버전 첫 실행 때 migrate 후 다시 띄운다(AppDelegate).
// 개발 빌드는 Sparkle 없이 컴파일된다(canImport).

import Foundation
#if canImport(Sparkle)
import Sparkle

final class Updater: NSObject, SPUUpdaterDelegate {
    static let shared = Updater()
    private var controller: SPUStandardUpdaterController?

    var available: Bool { controller != nil }

    func start() {
        let feed = Bundle.main.object(forInfoDictionaryKey: "SUFeedURL") as? String ?? ""
        let key = Bundle.main.object(forInfoDictionaryKey: "SUPublicEDKey") as? String ?? ""
        guard !feed.isEmpty, !key.isEmpty else { return }  // 배포 채널을 정하지 않은 빌드
        controller = SPUStandardUpdaterController(startingUpdater: true, updaterDelegate: self, userDriverDelegate: nil)
    }

    func checkForUpdates() {
        controller?.checkForUpdates(nil)
    }

    func updater(_ updater: SPUUpdater, willInstallUpdate item: SUAppcastItem) {
        Agents.stopForUpdate()
    }
}
#else
final class Updater {
    static let shared = Updater()
    var available: Bool { false }
    func start() {}
    func checkForUpdates() {}
}
#endif
