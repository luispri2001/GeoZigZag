import pygame
import odrive
from odrive.enums import *

pygame.init()
pygame.display.set_mode((200, 100))

odrv0 = odrive.find_any()
axis = odrv0.axis0

axis.requested_state = AXIS_STATE_CLOSED_LOOP_CONTROL
axis.controller.config.control_mode = CONTROL_MODE_VELOCITY_CONTROL

running = True

while running:
    pygame.event.pump()

    if pygame.key.get_pressed()[pygame.K_w]:
        axis.controller.input_vel = 1.0
    else:
        axis.controller.input_vel = 0.0

    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False

    pygame.time.wait(10)
