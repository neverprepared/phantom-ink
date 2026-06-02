cask "brainbox-runner" do
  version "0.1.7"
  sha256 "ddaace173a552091429f5da79c05bb6a43fa02853fc4b18e914c7ef5f181a311"

  url "https://github.com/neverprepared/phantom-ink/releases/download/runner/v#{version}/BrainboxRunner.dmg"
  name "Brainbox Runner"
  desc "Menu-bar runner that connects to a brainbox API and executes session work"
  homepage "https://github.com/neverprepared/phantom-ink"

  depends_on macos: ">= :ventura"

  app "BrainboxRunner.app"

  zap trash: [
    "~/Library/Preferences/com.neverprepared.brainbox-runner.plist",
    "~/Library/Application Scripts/com.neverprepared.brainbox-runner",
    "~/Library/Saved Application State/com.neverprepared.brainbox-runner.savedState",
  ]
end
