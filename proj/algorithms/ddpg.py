import torch
import numpy as np
from baselines import logger
from proj.utils.saver import SnapshotSaver
from proj.utils.tqdm_util import trange
from proj.utils.torch_util import update_polyak
from proj.common.models import ContinuousQFunction
from proj.common.sampling import ReplayBuffer
from proj.common.log_utils import save_config, log_reward_statistics
from proj.common.env_makers import VecEnvMaker


TOTAL_STEPS_DEFAULT = int(1e6)
REPLAY_SIZE_DEFAULT = int(1e6)


def ddpg(
    env,
    policy,
    q_func=None,
    total_steps=TOTAL_STEPS_DEFAULT,
    gamma=0.99,
    replay_size=REPLAY_SIZE_DEFAULT,
    polyak=0.995,
    start_steps=10000,
    epoch=5000,
    pi_lr=1e-3,
    qf_lr=1e-3,
    mb_size=100,
    act_noise=0.1,
    max_ep_length=1000,
    updates_per_step=1.0,
    **saver_kwargs
):

    # Set and save experiment hyperparameters
    q_func = q_func or ContinuousQFunction.from_policy(policy)
    save_config(locals())
    saver = SnapshotSaver(logger.get_dir(), locals(), **saver_kwargs)

    # Initialize environments, models and replay buffer
    vec_env = VecEnvMaker(env)()
    test_env = VecEnvMaker(env)(train=False)
    ob_space, ac_space = vec_env.observation_space, vec_env.action_space
    pi_class, pi_args = policy.pop("class"), policy
    qf_class, qf_args = q_func.pop("class"), q_func
    policy = pi_class(vec_env, **pi_args)
    q_func = qf_class(vec_env, **qf_args)
    replay = ReplayBuffer(replay_size, ob_space, ac_space)

    # Initialize optimizers and target networks
    loss_fn = torch.nn.MSELoss()
    pi_optim = torch.optim.Adam(policy.parameters(), lr=pi_lr)
    qf_optim = torch.optim.Adam(q_func.parameters(), lr=qf_lr)
    pi_targ = pi_class(vec_env, **pi_args)
    qf_targ = qf_class(vec_env, **qf_args)
    pi_targ.load_state_dict(policy.state_dict())
    qf_targ.load_state_dict(q_func.state_dict())

    # Save initial state
    saver.save_state(
        index=0,
        state=dict(
            alg=dict(samples=0),
            policy=policy.state_dict(),
            q_func=q_func.state_dict(),
            pi_optim=pi_optim.state_dict(),
            qf_optim=qf_optim.state_dict(),
            qf_targ=qf_targ.state_dict(),
        ),
    )

    # Setup and run policy tests
    ob, don = test_env.reset(), False

    @torch.no_grad()
    def test_policy():
        nonlocal ob, don
        for _ in range(10):
            while not don:
                act = policy.actions(torch.from_numpy(ob))
                ob, _, don, _ = test_env.step(act.numpy())
            don = False
        log_reward_statistics(test_env, num_last_eps=10, prefix="Test")

    test_policy()
    logger.logkv("Epoch", 0)
    logger.logkv("TotalNSamples", 0)
    log_reward_statistics(vec_env)
    logger.dumpkvs()

    # Set action sampling strategies
    def rand_uniform_actions(_):
        return np.stack([ac_space.sample() for _ in range(vec_env.num_envs)])

    @torch.no_grad()
    def noisy_policy_actions(obs):
        acts = policy(torch.from_numpy(obs))
        acts += act_noise * torch.randn_like(acts)
        return np.clip(acts.numpy(), ac_space.low, ac_space.high)

    # Algorithm main loop
    obs1, ep_length = vec_env.reset(), 0
    for samples in trange(1, total_steps + 1, desc="Training", unit="iter"):
        if samples <= start_steps:
            actions = rand_uniform_actions
        else:
            actions = noisy_policy_actions

        acts = actions(obs1)
        obs2, rews, dones, _ = vec_env.step(acts)
        ep_length += 1
        dones[0] = False if ep_length == max_ep_length else dones[0]

        as_tensors = map(torch.from_numpy, (obs1, acts, rews, obs2, dones.astype("f")))
        for ob1, act, rew, ob2, done in zip(*as_tensors):
            replay.store(ob1, act, rew, ob2, done)
        obs1 = obs2

        if (dones[0] or ep_length == max_ep_length) and replay.size >= mb_size:
            for _ in range(int(ep_length * updates_per_step)):
                ob_1, act_, rew_, ob_2, done_ = replay.sample(mb_size)
                with torch.no_grad():
                    targs = rew_ + gamma * (1 - done_) * qf_targ(ob_2, pi_targ(ob_2))
                qf_optim.zero_grad()
                qf_val = q_func(ob_1, act_)
                qf_loss = loss_fn(qf_val, targs)
                qf_loss.backward()
                qf_optim.step()

                pi_optim.zero_grad()
                qpi_val = q_func(ob_1, policy(ob_1)).mean()
                pi_loss = qpi_val.neg()
                pi_loss.backward()
                pi_optim.step()

                update_polyak(policy, pi_targ, polyak)
                update_polyak(q_func, qf_targ, polyak)

                logger.logkv_mean("Q1Val", qf_val.mean().item())
                logger.logkv_mean("Q1Loss", qf_loss.item())
                logger.logkv_mean("QPiVal", qpi_val.item())
                logger.logkv_mean("PiLoss", pi_loss.item())

            ep_length = 0

        if samples % epoch == 0:
            test_policy()
            logger.logkv("Epoch", samples // epoch)
            logger.logkv("TotalNSamples", samples)
            log_reward_statistics(vec_env)
            logger.dumpkvs()

            saver.save_state(
                index=samples // epoch,
                state=dict(
                    alg=dict(samples=samples),
                    policy=policy.state_dict(),
                    q_func=q_func.state_dict(),
                    pi_optim=pi_optim.state_dict(),
                    qf_optim=qf_optim.state_dict(),
                    pi_targ=pi_targ.state_dict(),
                    qf_targ=qf_targ.state_dict(),
                ),
            )
