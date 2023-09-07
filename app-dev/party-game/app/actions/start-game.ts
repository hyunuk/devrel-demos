/**
 * Copyright 2023 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

'use server';

import {gamesRef} from '@/app/lib/firebase-server-initialization';
import {GameIdSchema, Tokens, gameStates} from '@/app/types';
import {FieldValue} from 'firebase-admin/firestore';
import {validateTokens} from '@/app/lib/server-token-validator';

export async function startGameAction({gameId, tokens}: {gameId: string, tokens: Tokens}) {
  const authUser = await validateTokens(tokens);

  // Parse request (throw an error if not correct)
  GameIdSchema.parse(gameId);

  const gameRef = await gamesRef.doc(gameId);
  const gameDoc = await gameRef.get();
  const game = gameDoc.data();

  if (game.leader.uid !== authUser.uid) {
    // Respond with JSON indicating no game was found
    throw new Error('Only the leader of this game may start this game.');
  }

  // update database to start the game
  await gameRef.update({
    state: gameStates.AWAITING_PLAYER_ANSWERS,
    startTime: FieldValue.serverTimestamp(),
  });
}
