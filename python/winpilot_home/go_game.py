import random
import tkinter as tk


class GoGame:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("围棋小游戏")
        self.root.geometry("600x650")
        self.board_size = 9
        self.cell_size = 60
        self.board = [
            [0] * self.board_size for _ in range(self.board_size)
        ]
        self.current_player = 1
        self.move_count = 0

        self.canvas = tk.Canvas(
            self.root,
            width=540,
            height=540,
            bg="#DEB887",
        )
        self.canvas.pack(pady=10)

        self.status = tk.Label(
            self.root,
            text="黑棋先行",
            font=("Arial", 14),
        )
        self.status.pack()

        buttons = tk.Frame(self.root)
        buttons.pack(pady=5)
        tk.Button(
            buttons,
            text="新游戏",
            command=self.new_game,
        ).pack(side=tk.LEFT, padx=5)
        tk.Button(
            buttons,
            text="AI落子",
            command=self.ai_move,
        ).pack(side=tk.LEFT, padx=5)

        self.draw_board()
        self.canvas.bind("<Button-1>", self.on_click)

    def draw_board(self) -> None:
        self.canvas.delete("all")
        last = 30 + (self.board_size - 1) * self.cell_size
        for index in range(self.board_size):
            point = 30 + index * self.cell_size
            self.canvas.create_line(point, 30, point, last)
            self.canvas.create_line(30, point, last, point)

        for row in range(self.board_size):
            for column in range(self.board_size):
                stone = self.board[row][column]
                if stone == 0:
                    continue
                x = 30 + column * self.cell_size
                y = 30 + row * self.cell_size
                color = "black" if stone == 1 else "white"
                self.canvas.create_oval(
                    x - 12,
                    y - 12,
                    x + 12,
                    y + 12,
                    fill=color,
                    outline="gray",
                )

    def place_stone(self, row: int, column: int, *, actor: str = "") -> None:
        if self.board[row][column] != 0:
            return
        self.board[row][column] = self.current_player
        self.move_count += 1
        self.current_player = 3 - self.current_player
        next_player = "黑" if self.current_player == 1 else "白"
        prefix = f"{actor}已落子 | " if actor else ""
        self.status.config(
            text=f"{prefix}{next_player}棋落子 | 已下{self.move_count}手"
        )
        self.draw_board()

    def on_click(self, event: tk.Event) -> None:
        column = round((event.x - 30) / self.cell_size)
        row = round((event.y - 30) / self.cell_size)
        if 0 <= row < self.board_size and 0 <= column < self.board_size:
            self.place_stone(row, column)

    def ai_move(self) -> None:
        empty = [
            (row, column)
            for row in range(self.board_size)
            for column in range(self.board_size)
            if self.board[row][column] == 0
        ]
        if empty:
            row, column = random.choice(empty)
            self.place_stone(row, column, actor="AI")

    def new_game(self) -> None:
        self.board = [
            [0] * self.board_size for _ in range(self.board_size)
        ]
        self.current_player = 1
        self.move_count = 0
        self.status.config(text="黑棋先行")
        self.draw_board()

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    GoGame().run()
