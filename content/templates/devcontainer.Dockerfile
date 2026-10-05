# Generated at setup (dev container), and only when this repo matches more than one
# stack pack. A single-stack repo uses its pack's image directly and never builds this.
#
# Why a Dockerfile at all: a Java image has no Node, and a Node image has no JDK. The
# devcontainer *features* mechanism exists precisely to compose toolchains, but it is
# unavailable here — features apply when the devcontainer spec builds the image, and ours is
# defined by compose. So the primary pack supplies the base and every other matched pack
# contributes its install lines.
#
# Edit the stack packs rather than this file: it is regenerated.
FROM {{FROM}}

{{TOOLCHAIN_INSTALL}}
