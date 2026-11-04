# Notebooks

Scripts for digitising A5 notebooks and, together with pages exported from a
Supernote Manta, training a local diffusion LoRA. The notebooks, their scans,
the Supernote pages, the manifest describing them, and the trained weights are
kept out of this repository.

`lock-camera.sh` turns off the camera's automatic focus, exposure, and white
balance and pins each to a fixed value, so every frame is taken under the same
settings. It is run at the start of each session, since the camera forgets them
when unplugged.

`capture.sh <notebook-id>` saves one full-resolution frame per pedal press as
`raw/nbNNN_pPPP.png`, resuming from the last saved page. Needs `v4l-utils` and
`ffmpeg`.
