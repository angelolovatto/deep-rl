import os
import tempfile
from gym.envs.registration import register
os.environ['OPENAI_LOGDIR'] = os.path.join(tempfile.gettempdir(), 'deep-rl')


# === CartPole-v1 modifications ===
for stddev in (0.0, 0.5, 1.0, 1.5):
    register(
        id='Gauss{}CartPole-v1'.format(stddev),
        entry_point='proj.envs:RandomCartPoleEnv',
        kwargs={'noise_scale': stddev},
        max_episode_steps=500,
        reward_threshold=475.0,
    )
