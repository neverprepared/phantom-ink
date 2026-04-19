class ShellProfiler < Formula
  desc "Workspace profile manager using direnv for environment-specific configurations"
  homepage "https://github.com/neverprepared/phantom-ink"
  version "0.5.3"

  depends_on "direnv"

  on_macos do
    if Hardware::CPU.arm?
      url "https://github.com/neverprepared/phantom-ink/releases/download/shell-profiler/v#{version}/shell-profiler-v#{version}-darwin-arm64.tar.gz"
      sha256 "placeholder" # darwin-arm64
    end
    if Hardware::CPU.intel?
      url "https://github.com/neverprepared/phantom-ink/releases/download/shell-profiler/v#{version}/shell-profiler-v#{version}-darwin-amd64.tar.gz"
      sha256 "placeholder" # darwin-amd64
    end
  end

  on_linux do
    if Hardware::CPU.arm?
      url "https://github.com/neverprepared/phantom-ink/releases/download/shell-profiler/v#{version}/shell-profiler-v#{version}-linux-arm64.tar.gz"
      sha256 "placeholder" # linux-arm64
    end
    if Hardware::CPU.intel?
      url "https://github.com/neverprepared/phantom-ink/releases/download/shell-profiler/v#{version}/shell-profiler-v#{version}-linux-amd64.tar.gz"
      sha256 "placeholder" # linux-amd64
    end
  end

  def install
    bin.install "shell-profiler"
  end

  test do
    assert_match "Workspace Profile Manager", shell_output("#{bin}/shell-profiler help")
  end
end
