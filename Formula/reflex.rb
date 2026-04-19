class Reflex < Formula
  desc "Claude Code plugin for development workflows, skills, and MCP management"
  homepage "https://github.com/neverprepared/phantom-ink"
  url "https://github.com/neverprepared/phantom-ink/releases/download/reflex-v1.27.0/reflex-1.27.0.tar.gz"
  version "1.27.0"
  sha256 "placeholder"
  license "MIT"

  def install
    (share/"reflex").mkpath
    cp_r "plugins/reflex/.", share/"reflex"
  end

  def post_install
    target = Pathname.new(Dir.home)/"bin"/"reflex"
    target.parent.mkpath
    target.make_symlink share/"reflex" unless target.exist?
  end

  def caveats
    <<~EOS
      Plugin installed to #{share}/reflex
      Symlinked to ~/bin/reflex (created on post-install)

      To use reflex with Claude Code:

        claude --plugin-dir ~/bin/reflex

      Or install from the plugin marketplace:

        /plugin marketplace add mindmorass/reflex

      NOTE: `brew uninstall reflex` will NOT remove ~/bin/reflex (it's a symlink).
      To fully remove, also run:

        rm -rf ~/bin/reflex
    EOS
  end

  test do
    assert_path_exists share/"reflex/.claude-plugin/plugin.json"
  end
end
