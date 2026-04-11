class ShellProfiler < Formula
  desc "Workspace profile manager using direnv for environment-specific configurations"
  homepage "https://github.com/neverprepared/ink-bunny"
  version "0.5.3"

  depends_on "direnv"

  on_macos do
    if Hardware::CPU.arm?
      url "https://github.com/neverprepared/ink-bunny/releases/download/shell-profiler/v#{version}/shell-profiler-v#{version}-darwin-arm64.tar.gz"
      sha256 "5624771644acc34bf98e755305d96aee513b1589294e3f965dff4f2f144eb08e" # darwin-arm64
    end
    if Hardware::CPU.intel?
      url "https://github.com/neverprepared/ink-bunny/releases/download/shell-profiler/v#{version}/shell-profiler-v#{version}-darwin-amd64.tar.gz"
      sha256 "2e8f9876a71b910e58444199eec346043d89450085bf65ae22d33cbcdac5f930" # darwin-amd64
    end
  end

  on_linux do
    if Hardware::CPU.arm?
      url "https://github.com/neverprepared/ink-bunny/releases/download/shell-profiler/v#{version}/shell-profiler-v#{version}-linux-arm64.tar.gz"
      sha256 "fddecfddad8c0b94396f9d9e7101ad8d55c8bf43b1a441dd8a540ccffacadf55" # linux-arm64
    end
    if Hardware::CPU.intel?
      url "https://github.com/neverprepared/ink-bunny/releases/download/shell-profiler/v#{version}/shell-profiler-v#{version}-linux-amd64.tar.gz"
      sha256 "95cbd79ce2102f7ed81b7225f7131ab6bef5054fc9169c0bbf25f33a2a7110a5" # linux-amd64
    end
  end

  def install
    bin.install "shell-profiler"
  end

  test do
    assert_match "Workspace Profile Manager", shell_output("#{bin}/shell-profiler help")
  end
end
