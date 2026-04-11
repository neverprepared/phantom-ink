package templates

import (
	"bytes"
	_ "embed"
	"fmt"
	"text/template"
	"time"
)

//go:embed envrc.tpl
var envrcTemplate string

//go:embed env.tpl
var envTemplate string

//go:embed gitconfig.tpl
var gitconfigTemplate string

//go:embed starship.tpl
var starshipTemplate string

// EnvrcData holds the data for rendering the .envrc template
type EnvrcData struct {
	ProfileName string
	Template    string
	CreatedAt   string
}

// EnvData holds the data for rendering the .env template
type EnvData struct {
	ProfileName string
	Template    string
}

// GitconfigData holds the data for rendering the .gitconfig template
type GitconfigData struct {
	ProfileName string
	Template    string
	GitName     string
	GitEmail    string
}

// RenderEnvrc renders the .envrc template with the provided data
func RenderEnvrc(profileName, templateType string) (string, error) {
	tmpl, err := template.New("envrc").Parse(envrcTemplate)
	if err != nil {
		return "", fmt.Errorf("failed to parse .envrc template: %w", err)
	}

	data := EnvrcData{
		ProfileName: profileName,
		Template:    templateType,
		CreatedAt:   time.Now().UTC().Format("2006-01-02 15:04:05 UTC"),
	}

	var buf bytes.Buffer
	if err := tmpl.Execute(&buf, data); err != nil {
		return "", fmt.Errorf("failed to render .envrc template: %w", err)
	}

	return buf.String(), nil
}

// RenderEnv renders the .env template with the provided data
func RenderEnv(profileName, templateType string) (string, error) {
	tmpl, err := template.New("env").Parse(envTemplate)
	if err != nil {
		return "", fmt.Errorf("failed to parse .env template: %w", err)
	}

	data := EnvData{
		ProfileName: profileName,
		Template:    templateType,
	}

	var buf bytes.Buffer
	if err := tmpl.Execute(&buf, data); err != nil {
		return "", fmt.Errorf("failed to render .env template: %w", err)
	}

	return buf.String(), nil
}

// StarshipData holds the data for rendering the starship config template
type StarshipData struct {
	ProfileName string
	Template    string
	AccentColor string
	GlyphColor  string
}

// starshipAccentColors maps template types to their accent color and pre-darkened glyph color.
// Glyph color is ~10% darker than accent to compensate for font anti-aliasing on nerd font glyphs.
// When adding new profiles, hardcode the glyph color at ~10% darker than the palette accent value.
var starshipAccentColors = map[string][2]string{
	"personal": {"#19baff", "#16a7e5"}, // blue: iTerm personal blue, glyph darkened ~10%
	"work":     {"#28c940", "#24b53a"}, // green: iTerm work green, glyph darkened ~10%
	"client":   {"#ff9500", "#e58600"}, // orange: iTerm client orange, glyph darkened ~10%
	"basic":    {"#7e7f80", "#717273"}, // gray: iTerm basic gray, glyph darkened ~10%
}

// RenderStarship renders the starship config template for the given profile and template type.
// The generated file should be written to $HOME/.config/starship/starship-<profile>.toml.
func RenderStarship(profileName, templateType string) (string, error) {
	tmpl, err := template.New("starship").Parse(starshipTemplate)
	if err != nil {
		return "", fmt.Errorf("failed to parse starship template: %w", err)
	}

	colors, ok := starshipAccentColors[templateType]
	if !ok {
		colors = starshipAccentColors["basic"]
	}

	data := StarshipData{
		ProfileName: profileName,
		Template:    templateType,
		AccentColor: colors[0],
		GlyphColor:  colors[1],
	}

	var buf bytes.Buffer
	if err := tmpl.Execute(&buf, data); err != nil {
		return "", fmt.Errorf("failed to render starship template: %w", err)
	}

	return buf.String(), nil
}

// RenderGitconfig renders the .gitconfig template with the provided data
func RenderGitconfig(profileName, templateType, gitName, gitEmail string) (string, error) {
	tmpl, err := template.New("gitconfig").Parse(gitconfigTemplate)
	if err != nil {
		return "", fmt.Errorf("failed to parse .gitconfig template: %w", err)
	}

	// Default values if not provided
	if gitName == "" {
		gitName = "Your Name"
	}
	if gitEmail == "" {
		gitEmail = "your.email@example.com"
	}

	data := GitconfigData{
		ProfileName: profileName,
		Template:    templateType,
		GitName:     gitName,
		GitEmail:    gitEmail,
	}

	var buf bytes.Buffer
	if err := tmpl.Execute(&buf, data); err != nil {
		return "", fmt.Errorf("failed to render .gitconfig template: %w", err)
	}

	return buf.String(), nil
}
