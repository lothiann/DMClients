#ifndef ENGINE_CLIENT_BOT_CONTROL_H
#define ENGINE_CLIENT_BOT_CONTROL_H

#include <engine/console.h>
#include <engine/shared/config.h>

class IClient;

class CBotControl
{
public:
	bool m_Jump;
	bool m_Hook;
	bool m_Fire;
	int m_Direction;

	// Относительное смещение прицела (c_aim)
	int m_TargetX;
	int m_TargetY;

	// Абсолютный прицел (c_oaim)
	int m_OverrideTargetX;
	int m_OverrideTargetY;

	int64_t m_JumpEndTick;
	int64_t m_HookEndTick;
	int64_t m_FireEndTick;
	int64_t m_MoveEndTick;
	int64_t m_AimEndTick; // для c_aim
	int64_t m_OverrideAimEndTick; // для c_oaim

	IClient *m_pClient;
	IConsole *m_pConsole;

	CBotControl();

	void OnInit();
	void OnTick();

	bool GetBotJump() const { return m_Jump; }
	bool GetBotHook() const { return m_Hook; }
	bool GetBotFire() const { return m_Fire; }
	int GetBotMove() const { return m_Direction; }

	bool IsBotActiveJump();
	bool IsBotActiveHook();
	bool IsBotActiveFire();
	bool IsBotActiveMove();
	bool IsBotActiveAim();
	bool IsBotActiveOverrideAim();

	void ActionInput(const char *pName, int MS);
	void ActionAim(int dx, int dy);
	void ActionOverrideAim(int x, int y);
	void ActionStop();

private:
	static void ConInput(IConsole::IResult *pResult, void *pUserData);
	static void ConStop(IConsole::IResult *pResult, void *pUserData);
	static void ConAim(IConsole::IResult *pResult, void *pUserData);
	static void ConOverrideAim(IConsole::IResult *pResult, void *pUserData);
};

#endif