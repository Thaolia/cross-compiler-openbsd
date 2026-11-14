/*
 * Opens a window and animates a colour gradient for a few seconds.
 * Runs headless with SDL_VIDEODRIVER=dummy (used by the CI smoke test).
 */
#include <stdio.h>
#include <stdlib.h>
#include <SDL.h>

#define W 640
#define H 360

int
main(int argc, char *argv[])
{
	SDL_version v;
	SDL_Window *win;
	SDL_Renderer *ren;
	SDL_Event ev;
	Uint32 start;
	int seconds = argc > 1 ? atoi(argv[1]) : 3, running = 1, frames = 0;

	SDL_GetVersion(&v);
	if (SDL_Init(SDL_INIT_VIDEO) != 0) {
		fprintf(stderr, "SDL_Init: %s\n", SDL_GetError());
		return 1;
	}
	printf("SDL %d.%d.%d, video driver: %s\n", v.major, v.minor, v.patch,
	    SDL_GetCurrentVideoDriver());

	win = SDL_CreateWindow("sdl2demo (cross-compiled)", SDL_WINDOWPOS_CENTERED,
	    SDL_WINDOWPOS_CENTERED, W, H, 0);
	if (win == NULL) {
		fprintf(stderr, "SDL_CreateWindow: %s\n", SDL_GetError());
		SDL_Quit();
		return 1;
	}
	ren = SDL_CreateRenderer(win, -1, 0);
	if (ren == NULL)
		ren = SDL_CreateRenderer(win, -1, SDL_RENDERER_SOFTWARE);
	if (ren == NULL) {
		fprintf(stderr, "SDL_CreateRenderer: %s\n", SDL_GetError());
		SDL_DestroyWindow(win);
		SDL_Quit();
		return 1;
	}

	start = SDL_GetTicks();
	while (running && SDL_GetTicks() - start < (Uint32)seconds * 1000) {
		Uint32 t = SDL_GetTicks() - start;
		int x;

		while (SDL_PollEvent(&ev))
			if (ev.type == SDL_QUIT ||
			    (ev.type == SDL_KEYDOWN && ev.key.keysym.sym == SDLK_ESCAPE))
				running = 0;
		for (x = 0; x < W; x++) {
			SDL_SetRenderDrawColor(ren, (x + t / 4) % 256, (x * 255) / W,
			    255 - (x * 255) / W, 255);
			SDL_RenderDrawLine(ren, x, 0, x, H - 1);
		}
		SDL_RenderPresent(ren);
		frames++;
		SDL_Delay(16);
	}
	printf("rendered %d frames\n", frames);

	SDL_DestroyRenderer(ren);
	SDL_DestroyWindow(win);
	SDL_Quit();
	return 0;
}
